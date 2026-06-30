"""Copyright 2026.

SPDX-License-Identifier: Apache-2.0

Dynamic GitHub Profile README generator.
Generates light_mode.svg and dark_mode.svg from config data + GitHub API stats.
ASCII art is generated from avatar.png with a cyan monochrome gradient.
"""

import datetime
import hashlib
import os
import time
from pathlib import Path

import requests
from dateutil import relativedelta
from dotenv import load_dotenv
from PIL import Image

from config import BIRTHDAY, PROFILE

load_dotenv()

# ── constants ──────────────────────────────────────────────────────────────────

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
CACHE_DIR = Path("cache")
ASCII_RAMP = "@%#*+=-:. "
COMMENT_BLOCK_SIZE = 7
CACHE_COMMENT_LINE = "This line is a comment block. Write whatever you want here.\n"

# runtime state
HEADERS = {}
USER_NAME = ""
OWNER_ID = None

QUERY_COUNT = {
    "user_getter": 0,
    "follower_getter": 0,
    "graph_repos_stars": 0,
    "recursive_loc": 0,
    "loc_query": 0,
}


# ── environment & helpers ──────────────────────────────────────────────────────


def require_env(name):
    value = os.getenv(name)
    if value:
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def configure_environment():
    global HEADERS, USER_NAME
    access_token = require_env("ACCESS_TOKEN")
    USER_NAME = require_env("USER_NAME")
    HEADERS = {"authorization": f"token {access_token}"}


def cache_file_path():
    hashed_user = hashlib.sha256(USER_NAME.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{hashed_user}.txt"


def format_age(birthday):
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    parts = [
        f"{diff.years} year{'s' if diff.years != 1 else ''}",
        f"{diff.months} month{'s' if diff.months != 1 else ''}",
        f"{diff.days} day{'s' if diff.days != 1 else ''}",
    ]
    suffix = " \U0001f382" if diff.months == 0 and diff.days == 0 else ""
    return ", ".join(parts) + suffix


def query_count(function_name):
    QUERY_COUNT[function_name] += 1


def format_display_text(value):
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def build_dot_string(value_text, length):
    """Build dot padding that visually separates a label from its value."""
    just_len = max(0, length - len(value_text))
    if just_len <= 2:
        dot_map = {0: "", 1: " ", 2: ". "}
        return dot_map[just_len]
    return " " + ("." * just_len) + " "


def format_compact_number(value):
    """Shorten large numeric values for SVG display."""
    if isinstance(value, str):
        normalized = value.replace(",", "").strip().upper()
        if normalized.endswith("M") or normalized.endswith("K"):
            return value
        value = int(normalized)
    absolute_value = abs(value)
    if absolute_value >= 1_000_000:
        formatted = f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{formatted}M"
    if absolute_value >= 1_000:
        formatted = f"{value / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{formatted}K"
    return str(value)


# ── GraphQL helpers ────────────────────────────────────────────────────────────


def raise_request_error(operation_name, response):
    if response.status_code == 403:
        raise RuntimeError(
            "Too many requests in a short amount of time. GitHub returned 403."
        )
    raise RuntimeError(
        f"{operation_name} failed with status {response.status_code}: "
        f"{response.text}. Query counts: {QUERY_COUNT}"
    )


def graphql_request(operation_name, query, variables, partial_cache=None):
    try:
        response = requests.post(
            GITHUB_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=HEADERS,
            timeout=30,
        )
    except requests.RequestException as error:
        if partial_cache is not None:
            force_close_file(*partial_cache)
        raise RuntimeError(f"{operation_name} request failed: {error}") from error

    if response.status_code != 200:
        if partial_cache is not None:
            force_close_file(*partial_cache)
        raise_request_error(operation_name, response)

    try:
        payload = response.json()
    except ValueError as error:
        if partial_cache is not None:
            force_close_file(*partial_cache)
        raise RuntimeError(
            f"{operation_name} returned invalid JSON: {response.text}"
        ) from error

    if payload.get("errors"):
        if partial_cache is not None:
            force_close_file(*partial_cache)
        raise RuntimeError(
            f"{operation_name} returned GraphQL errors: {payload['errors']}"
        )

    return payload["data"]


# ── GitHub API functions ───────────────────────────────────────────────────────


def user_getter(username):
    query_count("user_getter")
    query = """
    query($login: String!){
        user(login: $login) {
            id
        }
    }"""
    data = graphql_request("user_getter", query, {"login": username})
    return data["user"]["id"]


def follower_getter(username):
    query_count("follower_getter")
    query = """
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }"""
    data = graphql_request("follower_getter", query, {"login": username})
    return int(data["user"]["followers"]["totalCount"])


def graph_repos_stars(count_type, owner_affiliation):
    total_repositories = 0
    total_stars = 0
    cursor = None

    query = """
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }"""

    while True:
        query_count("graph_repos_stars")
        variables = {
            "owner_affiliation": owner_affiliation,
            "login": USER_NAME,
            "cursor": cursor,
        }
        data = graphql_request("graph_repos_stars", query, variables)
        repositories = data["user"]["repositories"]
        total_repositories = repositories["totalCount"]
        total_stars += stars_counter(repositories["edges"])

        if not repositories["pageInfo"]["hasNextPage"]:
            break
        cursor = repositories["pageInfo"]["endCursor"]

    if count_type == "repos":
        return total_repositories
    if count_type == "stars":
        return total_stars
    return 0


def stars_counter(edges):
    total_stars = 0
    for edge in edges:
        total_stars += edge["node"]["stargazers"]["totalCount"]
    return total_stars


def recursive_loc(
    owner, repo_name, cache_rows, cache_header,
    addition_total=0, deletion_total=0, my_commits=0, cursor=None,
):
    query_count("recursive_loc")
    query = """
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            edges {
                                node {
                                    ... on Commit {
                                        author {
                                            user {
                                                id
                                            }
                                        }
                                        deletions
                                        additions
                                    }
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }"""
    variables = {"repo_name": repo_name, "owner": owner, "cursor": cursor}
    data = graphql_request(
        "recursive_loc", query, variables,
        partial_cache=(cache_rows, cache_header),
    )
    branch = data["repository"]["defaultBranchRef"]
    if branch is None:
        return 0, 0, 0

    history = branch["target"]["history"]
    return loc_counter_one_repo(
        owner, repo_name, cache_rows, cache_header,
        history, addition_total, deletion_total, my_commits,
    )


def loc_counter_one_repo(
    owner, repo_name, cache_rows, cache_header,
    history, addition_total, deletion_total, my_commits,
):
    for edge in history["edges"]:
        author = edge["node"].get("author") or {}
        user = author.get("user") or {}
        if user.get("id") == OWNER_ID:
            my_commits += 1
            addition_total += edge["node"]["additions"]
            deletion_total += edge["node"]["deletions"]

    if not history["pageInfo"]["hasNextPage"]:
        return addition_total, deletion_total, my_commits

    return recursive_loc(
        owner, repo_name, cache_rows, cache_header,
        addition_total, deletion_total, my_commits,
        history["pageInfo"]["endCursor"],
    )


def loc_query(owner_affiliation, comment_size=0, force_cache=False):
    query = """
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit {
                                        history {
                                            totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }"""

    cursor = None
    edges = []

    while True:
        query_count("loc_query")
        variables = {
            "owner_affiliation": owner_affiliation,
            "login": USER_NAME,
            "cursor": cursor,
        }
        data = graphql_request("loc_query", query, variables)
        repositories = data["user"]["repositories"]
        edges.extend(repositories["edges"])

        if not repositories["pageInfo"]["hasNextPage"]:
            break
        cursor = repositories["pageInfo"]["endCursor"]

    return cache_builder(edges, comment_size, force_cache)


# ── cache ──────────────────────────────────────────────────────────────────────


def comment_block_lines(comment_size):
    return [CACHE_COMMENT_LINE for _ in range(comment_size)]


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    cached = True
    filename = cache_file_path()

    try:
        with filename.open("r") as handle:
            data = handle.readlines()
    except FileNotFoundError:
        data = comment_block_lines(comment_size)
        with filename.open("w") as handle:
            handle.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        cached = False
        flush_cache(edges, filename, comment_size)
        with filename.open("r") as handle:
            data = handle.readlines()

    cache_header = data[:comment_size]
    cache_rows = data[comment_size:]

    for index, edge in enumerate(edges):
        repository_name = edge["node"]["nameWithOwner"]
        expected_hash = hashlib.sha256(repository_name.encode("utf-8")).hexdigest()
        stored_hash, stored_commit_count, *_ = cache_rows[index].split()

        if stored_hash != expected_hash:
            cache_rows[index] = f"{expected_hash} 0 0 0 0\n"
            stored_hash = expected_hash
            stored_commit_count = "0"

        branch = edge["node"].get("defaultBranchRef")
        history = None if branch is None else branch["target"]["history"]
        current_commit_count = 0 if history is None else history["totalCount"]

        if int(stored_commit_count) != current_commit_count:
            cached = False
            if current_commit_count == 0:
                cache_rows[index] = f"{stored_hash} 0 0 0 0\n"
                continue

            owner, repo_name = repository_name.split("/", 1)
            additions, deletions, my_commits = recursive_loc(
                owner, repo_name, cache_rows, cache_header,
            )
            cache_rows[index] = (
                f"{stored_hash} {current_commit_count} {my_commits} "
                f"{additions} {deletions}\n"
            )

    with filename.open("w") as handle:
        handle.writelines(cache_header)
        handle.writelines(cache_rows)

    for line in cache_rows:
        _, _, _, added_lines, deleted_lines = line.split()
        loc_add += int(added_lines)
        loc_del += int(deleted_lines)

    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    try:
        with filename.open("r") as handle:
            cache_header = handle.readlines()[:comment_size]
    except FileNotFoundError:
        cache_header = []

    if len(cache_header) < comment_size:
        cache_header.extend(comment_block_lines(comment_size - len(cache_header)))

    with filename.open("w") as handle:
        handle.writelines(cache_header[:comment_size])
        for edge in edges:
            repository_name = edge["node"]["nameWithOwner"]
            repository_hash = hashlib.sha256(repository_name.encode("utf-8")).hexdigest()
            handle.write(f"{repository_hash} 0 0 0 0\n")


def commit_counter(comment_size):
    total_commits = 0
    filename = cache_file_path()
    with filename.open("r") as handle:
        data = handle.readlines()
    for line in data[comment_size:]:
        total_commits += int(line.split()[2])
    return total_commits


def force_close_file(cache_rows, cache_header):
    filename = cache_file_path()
    with filename.open("w") as handle:
        handle.writelines(cache_header)
        handle.writelines(cache_rows)
    print(f"Saved partial cache data to {filename}.")


# ── ASCII art from image ───────────────────────────────────────────────────────


def image2ascii(image_path, theme="light", width=25):
    """Convert avatar.png to colored ASCII art.

    Returns list of rows, each row is list of (character, hex_color) tuples.
    """
    img = Image.open(image_path).convert("L")  # grayscale

    # Adjust height for monospace font (char height ≈ 2x char width)
    aspect = img.height / img.width
    height = int(width * aspect * 0.5)
    img = img.resize((width, height), Image.LANCZOS)

    ramp = ASCII_RAMP
    ramp_len = len(ramp) - 1

    # Cyan gradient endpoints per theme
    if theme == "dark":
        dark = (0, 77, 102)   # #004d66 — but keep it more visible on dark bg
        dark = (0, 96, 128)   # #006080 — slightly brighter for visibility
        bright = (0, 229, 255)  # #00e5ff
    else:
        dark = (0, 59, 77)     # #003b4d
        bright = (0, 229, 255)  # #00e5ff

    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            pixel = img.getpixel((x, y))
            char_index = int(pixel / 255 * ramp_len)
            char = ramp[char_index]

            # Linear interpolation for cyan color
            t = pixel / 255.0
            r = int(dark[0] + (bright[0] - dark[0]) * t)
            g = int(dark[1] + (bright[1] - dark[1]) * t)
            b = int(dark[2] + (bright[2] - dark[2]) * t)
            color = f"#{r:02x}{g:02x}{b:02x}"

            row.append((char, color))
        rows.append(row)

    return rows


# ── SVG builder ────────────────────────────────────────────────────────────────


def svg_builder(ascii_art, profile, stats, theme="light"):
    """Generate complete SVG string from ASCII art, profile config, and stats."""

    # ── color scheme ────────────────────────────────────────────────────────
    if theme == "dark":
        bg = "#0d1117"
        key_fill = "#ffa657"
        value_fill = "#a5d6ff"
        dot_fill = "#616e7f"
        main_fill = "#c9d1d9"
        add_fill = "#3fb950"
        del_fill = "#f85149"
    else:
        bg = "#f6f8fa"
        key_fill = "#953800"
        value_fill = "#0a3069"
        dot_fill = "#c2cfde"
        main_fill = "#24292f"
        add_fill = "#1a7f37"
        del_fill = "#cf222e"

    svg = []
    svg.append(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'font-family="Consolas,monospace" width="985px" height="530px" '
        'font-size="16px">\n'
        "<style>\n"
        ".key {fill: " + key_fill + ";}\n"
        ".value {fill: " + value_fill + ";}\n"
        ".addColor {fill: " + add_fill + ";}\n"
        ".delColor {fill: " + del_fill + ";}\n"
        ".cc {fill: " + dot_fill + ";}\n"
        "text, tspan {white-space: pre;}\n"
        "</style>\n"
        f'<rect width="985px" height="530px" fill="{bg}" rx="15"/>\n'
    )

    # ── left panel: ASCII art ────────────────────────────────────────────────
    svg.append(f'<text x="15" y="30" fill="{main_fill}" class="ascii">\n')
    ascii_x = 15
    char_width = 10  # approximate monospace char width at 16px Consolas
    for i, row in enumerate(ascii_art):
        y = 30 + i * 20
        svg.append(f'  <tspan x="{ascii_x}" y="{y}">')
        for char, color in row:
            svg.append(f'<tspan fill="{color}">{char}</tspan>')
        svg.append("</tspan>\n")
    svg.append("</text>\n")

    # ── right panel: info + stats ────────────────────────────────────────────
    right_x = 390
    svg.append(f'<text x="{right_x}" y="30" fill="{main_fill}">\n')

    # helper to add a row
    def add_row(y, label, value, target_width=54):
        dots = build_dot_string(value, target_width)
        svg.append(
            f'  <tspan x="{right_x}" y="{y}" class="cc">. </tspan>'
            f'<tspan class="key">{label}</tspan>:'
            f'<tspan class="cc">{dots}</tspan>'
            f'<tspan class="value">{value}</tspan>\n'
        )

    def add_row_raw(y, text, css_class=""):
        cls_attr = f' class="{css_class}"' if css_class else ""
        svg.append(f'  <tspan x="{right_x}" y="{y}"{cls_attr}>{text}</tspan>\n')

    # username@hostname header
    header = f"{profile['username']}@{profile['hostname']}"
    dashes = "\u2014" * 40
    add_row_raw(30, header + " " + dashes)

    # section: main info
    add_row(50, "OS", profile["os"], 54)
    add_row(70, "Uptime", stats["age"], 49)
    add_row(90, "Host", profile["host"], 54)
    add_row(110, "Kernel", profile["kernel"], 54)
    add_row(130, "IDE", profile["ide"], 54)
    add_row_raw(150, ". ")

    # section: languages
    add_row(170, "Languages.Programming", profile["languages_programming"], 54)
    add_row(190, "Languages.Computer", profile["languages_computer"], 54)
    add_row(210, "Languages.Real", profile["languages_real"], 54)
    add_row_raw(230, ". ")

    # section: hobbies
    add_row(250, "Hobbies.Software", profile["hobbies_software"], 54)
    add_row(270, "Hobbies.Hardware", profile["hobbies_hardware"], 54)
    add_row(290, "Hobbies.Others", profile["hobbies_others"], 54)
    add_row_raw(320, "- Contact " + ("\u2014" * 36))

    # section: contact
    add_row(350, "Email.Personal", profile["contact_email_personal"], 54)
    add_row(370, "Email.Work", profile["contact_email_work"], 54)
    add_row(390, "Instagram", profile["contact_instagram"], 54)
    add_row(410, "Discord", profile["contact_discord"], 54)
    add_row_raw(450, "- GitHub Stats " + ("\u2014" * 33))

    # section: GitHub stats
    repos_text = format_display_text(stats["repos"])
    contrib_text = format_display_text(stats["contributed"])
    stars_text = format_display_text(stats["stars"])
    commits_text = format_display_text(stats["commits"])
    followers_text = format_display_text(stats["followers"])
    loc_net = format_display_text(stats["loc_net"])
    loc_add = format_compact_number(stats["loc_add"])
    loc_del = format_compact_number(stats["loc_del"])

    # repos row (with contributed and stars)
    repos_dots = build_dot_string(repos_text, 6)
    stars_dots = build_dot_string(stars_text, 14)
    svg.append(
        f'  <tspan x="{right_x}" y="470" class="cc">. </tspan>'
        f'<tspan class="key">Repos</tspan>:'
        f'<tspan class="cc">{repos_dots}</tspan>'
        f'<tspan class="value">{repos_text}</tspan>'
        f' {{<tspan class="key">Contributed</tspan>: '
        f'<tspan class="value">{contrib_text}</tspan>}}'
        f'<tspan class="cc"> |  </tspan>'
        f'<tspan class="key">Stars</tspan>:'
        f'<tspan class="cc">{stars_dots}</tspan>'
        f'<tspan class="value">{stars_text}</tspan>\n'
    )

    # commits row (with followers)
    commits_dots = build_dot_string(commits_text, 22)
    followers_dots = build_dot_string(followers_text, 10)
    svg.append(
        f'  <tspan x="{right_x}" y="490" class="cc">. </tspan>'
        f'<tspan class="key">Commits</tspan>:'
        f'<tspan class="cc">{commits_dots}</tspan>'
        f'<tspan class="value">{commits_text}</tspan>'
        f'<tspan class="cc"> |  </tspan>'
        f'<tspan class="key">Followers</tspan>:'
        f'<tspan class="cc">{followers_dots}</tspan>'
        f'<tspan class="value">{followers_text}</tspan>\n'
    )

    # LOC row
    loc_dots = build_dot_string(loc_net, 25)
    svg.append(
        f'  <tspan x="{right_x}" y="510" class="cc">. </tspan>'
        f'<tspan class="key">GitHub LOC</tspan>:'
        f'<tspan class="cc">{loc_dots}</tspan>'
        f'<tspan class="value">{loc_net}</tspan>'
        f' ( <tspan class="addColor">+</tspan>'
        f'<tspan class="addColor">{loc_add}</tspan>, '
        f'<tspan class="delColor">-</tspan>'
        f'<tspan class="delColor">{loc_del}</tspan> )\n'
    )

    svg.append("</text>\n")
    svg.append("</svg>\n")

    return "".join(svg)


# ── timing helper ──────────────────────────────────────────────────────────────


def perf_counter(function, *args):
    start = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - start


def print_duration(label, duration):
    metric = f"{duration:.4f} s" if duration > 1 else f"{duration * 1000:.4f} ms"
    print(f"   {label + ':':<20}{metric:>12}")


# ── main ───────────────────────────────────────────────────────────────────────


def main():
    global OWNER_ID

    configure_environment()
    username = PROFILE["username"]

    print("Calculation times:")

    # 1. user id
    OWNER_ID, user_time = perf_counter(user_getter, username)
    print_duration("account data", user_time)

    # 2. age
    age_data, age_time = perf_counter(
        format_age, datetime.datetime(*BIRTHDAY)
    )
    print_duration("age calculation", age_time)

    # 3. LOC
    total_loc, loc_time = perf_counter(
        loc_query,
        ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"],
        COMMENT_BLOCK_SIZE,
    )
    cached_label = "LOC (cached)" if total_loc[-1] else "LOC (no cache)"
    print_duration(cached_label, loc_time)

    # 4. commits
    commit_data, commit_time = perf_counter(commit_counter, COMMENT_BLOCK_SIZE)
    print_duration("commit count", commit_time)

    # 5. stars
    star_data, star_time = perf_counter(graph_repos_stars, "stars", ["OWNER"])
    print_duration("stars", star_time)

    # 6. repos
    repo_data, repo_time = perf_counter(graph_repos_stars, "repos", ["OWNER"])
    print_duration("repos", repo_time)

    # 7. contributed repos
    contrib_data, contrib_time = perf_counter(
        graph_repos_stars, "repos", ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"],
    )
    print_duration("contributed repos", contrib_time)

    # 8. followers
    follower_data, follower_time = perf_counter(follower_getter, username)
    print_duration("followers", follower_time)

    stats = {
        "age": age_data,
        "repos": repo_data,
        "stars": star_data,
        "commits": commit_data,
        "followers": follower_data,
        "loc_add": total_loc[0],
        "loc_del": total_loc[1],
        "loc_net": total_loc[2],
        "contributed": contrib_data,
    }

    # 9. ASCII art
    print("Generating ASCII art...")
    ascii_light, ascii_time = perf_counter(image2ascii, "avatar.png", "light", 25)
    print_duration("ascii art (light)", ascii_time)
    ascii_dark, _ = perf_counter(image2ascii, "avatar.png", "dark", 25)

    # 10. Generate SVGs
    print("Generating SVGs...")
    with open("light_mode.svg", "w", encoding="utf-8") as f:
        f.write(svg_builder(ascii_light, PROFILE, stats, theme="light"))

    with open("dark_mode.svg", "w", encoding="utf-8") as f:
        f.write(svg_builder(ascii_dark, PROFILE, stats, theme="dark"))

    # timing summary
    total_runtime = (
        user_time + age_time + loc_time + commit_time
        + star_time + repo_time + contrib_time + follower_time + ascii_time
    )
    print(f"{'Total function time:':<21} {total_runtime:>11.4f} s")
    print(f"Total GitHub GraphQL API calls: {sum(QUERY_COUNT.values()):>3}")
    for function_name, count in QUERY_COUNT.items():
        print(f"   {function_name + ':':<25} {count:>6}")

    print("\nDone! light_mode.svg and dark_mode.svg generated.")


if __name__ == "__main__":
    main()
