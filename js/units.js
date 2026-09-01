/**
 * Pack units — the shared "quantity type" vocabulary.
 *
 * A pack unit is a counting unit, not a conversion factor: a "dozen" item holds
 * a quantity of dozens at a price per dozen, never 12 pieces at price/12. "" and
 * "pcs" are the plain per-piece case and keep the original wording ("₹100/item",
 * a bare qty).
 *
 * models.ALLOWED_UNITS is the server-side twin of UNIT_OPTIONS — a value missing
 * from that set comes back as a 400, so the two lists have to stay in step.
 */

export const UNIT_ONE = { dozen: 'dozen', box: 'box', pack: 'pack' };
export const UNIT_MANY = { dozen: 'dozen', box: 'boxes', pack: 'packs' };

// value → label, in the order every selector lists them. "pcs" leads because it
// is what an item has when nobody picks anything.
const UNIT_OPTIONS = [
  ['pcs', 'PCS'],
  ['dozen', 'Dozen'],
  ['box', 'Box'],
  ['pack', 'Pack'],
];

/** The pack unit as a key of UNIT_ONE/UNIT_MANY, or '' for the per-piece case. */
export function packUnit(unit) {
  const u = (unit || '').toLowerCase();
  return UNIT_ONE[u] ? u : '';
}

/** <option> markup for every unit, with `selected` pre-chosen. */
export function unitOptionsHtml(selected) {
  const sel = (selected || 'pcs').toLowerCase();
  return UNIT_OPTIONS
    .map(([v, label]) => `<option value="${v}"${v === sel ? ' selected' : ''}>${label}</option>`)
    .join('');
}

/**
 * Fill every `select[data-unit-select]` under `root`, honouring a `data-unit`
 * attribute as the pre-selection. The static selectors in index.html ship with
 * no options of their own so that this file stays the only place the list lives.
 */
export function fillUnitSelects(root = document) {
  root.querySelectorAll('select[data-unit-select]').forEach(sel => {
    sel.innerHTML = unitOptionsHtml(sel.dataset.unit);
  });
}

/** "5 dozen", "1 box", "3 boxes" — a bare "5" for pieces. */
export function qtyWithUnit(qty, unit) {
  const u = packUnit(unit);
  if (!u) return `${qty}`;
  return `${qty} ${Math.abs(Number(qty)) === 1 ? UNIT_ONE[u] : UNIT_MANY[u]}`;
}

/** " per dozen" / '' — the suffix that turns "Price" into "Price per dozen". */
export function perUnit(unit) {
  const u = packUnit(unit);
  return u ? ` per ${UNIT_ONE[u]}` : '';
}
