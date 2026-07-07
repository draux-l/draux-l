# ASCII Art Pipeline — Technical Reference

> Basado en el motor de [ascii-vision](https://github.com/anomalyco/ascii-vision).  
> Documento de referencia para implementar el mismo flujo en otros entornos.

---

## Arquitectura General

```
Imagen (HTMLImageElement / Buffer / Path)
       │
       ▼
┌──────────────────────────────┐
│  1. Downsampling             │  Escala la imagen a ancho × alto en caracteres
│     + Aspect Correction      │  Aplica corrección por proporción del font
└──────────┬───────────────────┘
           ▼
┌──────────────────────────────┐
│  2. Sharpening (opcional)    │  Unsharp mask 4-vecina
└──────────┬───────────────────┘
           ▼
┌──────────────────────────────┐
│  3. Luminancia BT.709        │  Luma perceptual + auto-contraste
│     + Contraste/Bright/Gamma │  + ajustes del usuario
└──────────┬───────────────────┘
           ▼
┌──────────────────────────────┐
│  4. Dithering (opcional)     │  Floyd-Steinberg error diffusion
└──────────┬───────────────────┘
           ▼
┌──────────────────────────────┐
│  5. Mapeo a caracteres       │  Luma → índice lineal vs ramp
│     + Color                  │  Source, palette o neon procedural
└──────────┬───────────────────┘
           ▼
┌──────────────────────────────┐
│  6. Render / Export          │  Canvas (fillText), TXT, PNG, clipboard
└──────────────────────────────┘
```

---

## Paso 1 — Downsampling

### Objetivo
Reducir la imagen a una grilla de `width × height` donde cada celda = 1 carácter.

### Cálculo de dimensiones

```
scale = targetWidth / imageWidth
aspectCorrection = 1 / cellAspectRatio   // ej: 1 / 0.6 ≈ 1.667
targetHeight = round((imageHeight * scale) / aspectCorrection)
```

**`cellAspectRatio`** es el ancho/alto del carácter monospace (~0.6 para latino, ~0.9 para CJK).  
La corrección evita que la imagen se vea aplastada verticalmente.

### Implementación (browser)

```typescript
const canvas = document.createElement('canvas');
canvas.width = targetWidth;
canvas.height = targetHeight;
const ctx = canvas.getContext('2d');
ctx.imageSmoothingEnabled = true;
ctx.imageSmoothingQuality = 'high';
ctx.drawImage(image, 0, 0, targetWidth, targetHeight);
const imageData = ctx.getImageData(0, 0, targetWidth, targetHeight);
const pixels = imageData.data; // Uint8ClampedArray, RGBA interleaved
```

Si no tenés canvas (Node, Python), usá una bilineal/bicúbica simple o una librería como `sharp`/`PIL`.

---

## Paso 2 — Sharpening (opcional)

### Unsharp Mask 4-vecina

Kernel de convolución:

```
  0   -s    0
  -s  1+4s  -s
  0   -s    0
```

Donde `s = options.sharpen` (0 = sin sharpening, 2 = máximo).  
Se aplica a R, G, B independientemente. Se saltea el borde de 1 píxel.

```typescript
for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
        const i = (y * w + x) * 4;
        for (let c = 0; c < 3; c++) {
            const val = copy[i+c]
                + (copy[i+c]*4 - copy[i-w*4+c] - copy[i+w*4+c] - copy[i-4+c] - copy[i+4+c]) * s;
            pixels[i+c] = clamp(val, 0, 255);
        }
    }
}
```

> **Opcional**: si querés simplicidad, saltate este paso. El resultado sigue siendo bueno.

---

## Paso 3 — Luminancia y Ajustes

### BT.709 Luma (perceptual)

```
luma = (0.2126 * R + 0.7152 * G + 0.0722 * B) / 255
```

Esta fórmula pondera el verde porque el ojo humano es más sensible a ese canal.

### Auto-Contraste (Min-Max stretch)

Se trackea `minLuma` y `maxLuma` de toda la imagen, luego:

```
luma = (luma - minLuma) / (maxLuma - minLuma)
```

Esto estira el rango dinámico a [0, 1] completo.

### Ajustes del usuario (en orden)

```
luma = (luma - 0.5) * contrast + 0.5    // contraste
luma += (brightness - 1)                // brillo
luma = pow(max(0, luma), 1 / gamma)     // gamma
luma = clamp(luma, 0, 1)                // saturar
```

Valores típicos: `contrast = 2.0`, `brightness = 1.2`, `gamma = 1.2`.

---

## Paso 4 — Dithering Floyd-Steinberg (opcional)

### Cuantización + error diffusion

Distribuye el error de redondeo a 4 vecinos:

```
píxel actual (*) → 7/16 →
3/16 → 5/16 → 1/16
```

```typescript
const steps = ramp.length - 1;    // niveles de cuantización

for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
        const i = y * w + x;
        const oldVal = luminanceMap[i];
        const newVal = Math.round(oldVal * steps) / steps;
        const error = oldVal - newVal;
        luminanceMap[i] = newVal;

        if (x + 1 < w)         luminanceMap[i + 1]         += error * 7/16;
        if (y + 1 < h) {
            if (x - 1 >= 0)    luminanceMap[i + w - 1]     += error * 3/16;
                               luminanceMap[i + w]         += error * 5/16;
            if (x + 1 < w)     luminanceMap[i + w + 1]     += error * 1/16;
        }
    }
}
```

> Sin dithering, zonas de gradiente suave se ven como bandas horizontales. Con dithering, se conserva la profundidad tonal.

---

## Paso 5 — Mapeo a Caracteres

### Character Ramps

Un `ramp` es un string ordenado de más denso (oscuro) a menos denso (claro).

```
"@%#*+=-:. "              // Strong (10 chars, default)
"$@B%8&WM#*oahkbdpqwm..." // Quality (~66 chars)
" ░▒▓█"                   // Blocks (5 chars)
```

### Selección de carácter

```typescript
const ramp = invert ? [...baseRamp].reverse() : baseRamp;

// classic mode
const charIdx = Math.floor(luma * (ramp.length - 1));
const char = ramp[charIdx];
```

### Modos de rendering

| Modo | Lógica |
|------|--------|
| **classic** | `ramp[floor(luma * (len-1))]` — mapeo lineal completo |
| **single-char** | `luma > 0.25 ? glyph : ' '` — poster minimalista |
| **scanline** | Pares: `luma > 0.3 ? '⎯' : ' '`; Impares: classic |
| **neon** | Misma selección que classic, colores reemplazados proceduralmente |

---

## Paso 5b — Color

### Source Colors (modo color verdadero)

```typescript
return `rgb(${r},${g},${b})`;   // color original del píxel
```

### Palette Colors (cuantización por paleta)

Distancia perceptual ponderada por luminancia:

```typescript
distance = dr² * 0.2126 + dg² * 0.7152 + db² * 0.0722
```

Se busca el color más cercano en la paleta.

### Neon Colors (modo procedural)

- Se calcula `luma` y `alpha = 0.5 + luma * 0.5`
- Cada paleta neon define colores base fijos (p.ej. matrix = `rgb(0, 255, 65)`)
- Se aplica bloom wash: mezcla con color flash usando `bloomIntensity`
- Se usa gradiente lineal por tramos para paletas como `red` y `ember`

### ¿Cuándo se devuelven colores?

```
colors = (colorMode === 'color' || mode === 'neon' || mode === 'single-char')
    ? finalColors : undefined
```

---

## Estructura de datos

### Input

```typescript
interface AsciiOptions {
    width: number;          // Ancho en caracteres (20-300)
    brightness: number;     // 0-2 (default 1.2)
    contrast: number;       // 0-2 (default 2.0)
    gamma: number;          // 0.1-3 (default 1.2)
    sharpen: number;        // 0-2
    ramp: string;           // Character ramp
    invert: boolean;        // Invertir ramp
    colorMode: 'grayscale' | 'color';
    aspectRatio: number;    // Corrección de aspect (1/cellAspectRatio)
    dithering: boolean;     // Floyd-Steinberg on/off
    mode: RenderingMode;    // 'classic' | 'single-char' | 'scanline' | 'neon'
}
```

### Output

```typescript
interface AsciiResult {
    text: string;           // ASCII con \n por fila
    colors?: string[][];    // [fila][columna] → "rgb(r,g,b)" o "rgba(r,g,b,a)"
    width: number;          // Grilla en caracteres
    height: number;         // Filas
}
```

---

## Valores por Defecto (recomendados)

```typescript
{
    width: 300,
    brightness: 1.2,
    contrast: 2.0,
    gamma: 1.2,
    ramp: "@%#*+=-:. ",
    invert: true,
    colorMode: 'grayscale',
    aspectRatio: 1 / 0.6,     // ≈ 1.667 (para latino)
    dithering: true,
    sharpen: 0,
    mode: 'classic'
}
```

---

## Referencias en el código original

| Archivo | Líneas | Contenido |
|---------|--------|-----------|
| `src/lib/ascii-engine.ts` | 292–441 | `convertToAscii()` — pipeline completo |
| `src/lib/ascii-engine.ts` | 65–67 | Ramps y aspect ratio |
| `src/lib/ascii-engine.ts` | 85–156 | Glyph language profiles |
| `src/lib/ascii-engine.ts` | 178–233 | Paletas de color |
| `src/lib/ascii-engine.ts` | 253–290 | Color mapping helpers |
| `src/lib/ascii-engine.ts` | 319–331 | Sharpening |
| `src/lib/ascii-engine.ts` | 333–368 | Luma + auto-contraste |
| `src/lib/ascii-engine.ts` | 374–391 | Floyd-Steinberg dithering |
| `src/lib/ascii-engine.ts` | 393–441 | Mapeo a caracteres + color |

---

## Notas para portabilidad

1. **Downsampling**: es el paso más dependiente del entorno. En browser usá `<canvas>`, en Node usá `sharp`, en Python usá `PIL.Image.resize` con `LANCZOS`.
2. **Aspect ratio**: no te olvides. Sin corrección, el ASCII art se ve aplastado.
3. **Orden de los ajustes**: auto-contraste → contraste → brillo → gamma. Si cambiás el orden, el resultado visual cambia.
4. **Dithering**: solo mejora si el ramp tiene menos de ~20 caracteres. Con ramps largos (quality) apenas se nota.
5. **Performance**: el loop de caracteres es O(ancho × alto). Para imágenes de 300×200 (60k chars) es instantáneo. Para 1000×700 puede tardar ~500ms.
