// The chess pieces, drawn here: each is a few shapes in a 100 x 100 box. `.d` shapes are details (an eye, a slit)
// drawn in the other colour. The same shapes serve both sides; style.css colours them.
const BASE = '<path d="M24 89v-6c0-4 4-7 8-7h36c4 0 8 3 8 7v6z"/>';
const PIECES = {
  p: '<path d="M37 76c2-13 9-19 9-30h8c0 11 7 17 9 30z"/><circle cx="50" cy="34" r="12.5"/>' + BASE,
  r: '<path d="M35 76l3-34h24l3 34z"/><path d="M30 44V21h9v8h7v-8h8v8h7v-8h9v23z"/>' + BASE,
  n: '<path d="M31 76c0-16 15-22 13-34-6 4-12 10-18 8l-5-6c3-8 9-14 15-22V11l8 8c18 1 28 19 26 57z"/><circle class="d" cx="38" cy="30" r="2.6"/>' + BASE,
  b: '<path d="M38 76c2-10 8-14 8-22h8c0 8 6 12 8 22z"/><path d="M50 15c10 10 14 20 9 33H41c-5-13-1-23 9-33z"/><circle cx="50" cy="12" r="4"/><path class="d" d="M55 27l-8 11" fill="none" stroke-width="3" stroke-linecap="round"/><path d="M39 48h22v6H39z"/>' + BASE,
  q: '<path d="M32 76L22 37l12 19 2-29 8 27 6-31 6 31 8-27 2 29 12-19-10 39z"/><circle cx="22" cy="33" r="4.5"/><circle cx="36" cy="23" r="4.5"/><circle cx="50" cy="19" r="4.5"/><circle cx="64" cy="23" r="4.5"/><circle cx="78" cy="33" r="4.5"/>' + BASE,
  k: '<path d="M32 76c-10-14-8-32 6-32 7 0 12 6 12 14 0-8 5-14 12-14 14 0 16 18 6 32z"/><path d="M47 46V29h-8v-6h8v-9h6v9h8v6h-8v17z"/>' + BASE,
};
// A piece as markup. `piece` is a FEN letter: upper case is White.
function pieceSvg(piece) {
  const white = piece === piece.toUpperCase();
  return `<svg class="pc ${white ? "w" : "b"}" viewBox="0 0 100 100" aria-hidden="true">${PIECES[piece.toLowerCase()]}</svg>`;
}
