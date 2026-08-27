/**
 * Layout — viewport tier state
 *
 * The single place the app asks "how much room do we have?". Everything else
 * about responsiveness is CSS; this exists only for the cases where the *markup*
 * differs between tiers, not just its styling.
 *
 * That's the master–detail pages (Ledger, Orders, Suppliers, History). Below
 * 1024px a record's detail is rendered inside its own card and revealed by
 * tapping it (an accordion). At 1024px and up the detail is rendered into a
 * separate pane beside the list. Same HTML, different parent — so the render
 * has to know which one to build, and has to rebuild when the tier changes.
 *
 * Keep these values in sync with the tier comment block at the top of
 * styles.css. CSS custom properties can't be used inside @media, so the
 * breakpoints are duplicated between here and there by necessity.
 */

/** Master–detail split activates here. Matches the styles.css desktop tier. */
export const WIDE_QUERY = window.matchMedia("(min-width: 1024px)");

/** Docked sidebar / full-viewport shell activates here (CSS-only, exported for completeness). */
export const SHELL_QUERY = window.matchMedia("(min-width: 768px)");

export function isWide() {
  return WIDE_QUERY.matches;
}

export function isShell() {
  return SHELL_QUERY.matches;
}

/**
 * Run `fn` whenever the app crosses the master–detail breakpoint.
 *
 * Page modules use this to re-render: rotating an iPad from landscape to
 * portrait would otherwise leave a detail pane rendered into a grid column
 * that no longer exists, and the reverse leaves every card collapsed with an
 * empty pane next to it.
 *
 * Fires only on the crossing, not on every resize, so re-rendering here is
 * cheap — all four pages already hold their full dataset in memory and
 * re-render from it on sort and search without refetching.
 */
export function onLayoutChange(fn) {
  WIDE_QUERY.addEventListener("change", fn);
}

/**
 * Run `fn` whenever the app crosses into or out of the full-viewport shell.
 *
 * Used by pages whose *content* changes at 768px rather than their structure —
 * the inventory grid, which only has room for cost price and margin once the
 * shell is wide.
 */
export function onShellChange(fn) {
  SHELL_QUERY.addEventListener("change", fn);
}
