# Diagram Grammar

Visual contract for `assets/codemap-template.html`. HTML is a published artifact.

## Surface

- Default paper is `#050505`. Pattern is a 16px grid of 1px lines at ~1% white.
- Header lives on the canvas, top-left: repo name plus tiny commit and scope. No page strip.
- Floating card-color legend lives on the canvas. Filters stay collapsed until opened.
- Title, legend, and filter chrome use 12px radius and a 2px border at 2% opacity.
- System fonts. No webfonts, no network, no external CSS or images.

## Cards and color

- Cards are larger plates with no stroke. Top-left and bottom-right corners are more rounded than the other two.
- Card copy uses the system sans. Fill is a slightly muted group hue.
- A three-stop wash uses the card hue mixed darker, running top to bottom on a diagonal so it does not read as button lighting.
- A hard 1px inner lip stays on the top and bottom edges. Matte, not glossy. No side vignette and no white specular.
- Copy is larger, left-aligned, clipped to the card, and fitted with the same SVG font.
- Group hues are blues, violets, magentas, reds, oranges, golds first. Greens last. Keep saturation a step below neon.
- Connectors keep a gutter away from card edges.
- Edge colors stay typed and saturated. Do not use emerald as a primary.

## Path highlight

- Hover or select a card lights its full caller and callee path.
- Hot edges keep a solid stroke and add a dashed march in the connection direction.
- Connector hit area stays wide. Unconnected nodes and edges go more transparent. Do not redraw the graph on hover.
- Hover or select inverts the origin card wash. Do not scale or move the graph on hover.

## Checklist

- [ ] Connectors are axis aligned.
- [ ] Each literal edge type has a distinct color and an arrow.
- [ ] Unknown edges are dashed.
- [ ] No blur, glow, or network request.
- [ ] Search, selection, hover path, and caller/callee highlight work on `file:`.
