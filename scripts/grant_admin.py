"""Grant or revoke support-panel access.

Access to the admin panel is a Firebase custom claim, `admin: true`, on a normal
user account. The claim travels inside the ID token, so the server checks it
without an extra read and the browser can tell whether to render the panel at
all — but it also means the claim can only be set out-of-band, by a tool holding
the Admin SDK credentials. That tool is this script.

    python scripts/grant_admin.py you@example.com           # dry run — reports only
    python scripts/grant_admin.py you@example.com --apply    # grants access
    python scripts/grant_admin.py you@example.com --apply --revoke
    python scripts/grant_admin.py --list                     # who currently has it

The target may be a uid, an email, or a phone number (+911234567890, or ten
digits and +91 is assumed).

The dry run is the default because a granted account can read and edit *every*
shop in the database.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

# Only main.py loads .env, and this script deliberately does not import the app.
# Everything below imports after it, hence the E402 suppressions: the
# environment has to exist before auth is imported.
load_dotenv(REPO_ROOT / ".env")

from firebase_admin import auth as fb_auth  # noqa: E402

from auth import init_firebase  # noqa: E402


def _find_user(target: str):
    """Resolve a uid, email or phone number to a Firebase user record."""
    attempts = []
    if "@" in target:
        attempts.append(("email", lambda: fb_auth.get_user_by_email(target)))
    digits = target.lstrip("+")
    if target.startswith("+") or digits.isdigit():
        attempts.append(("phone", lambda: fb_auth.get_user_by_phone_number("+" + digits)))
        if len(digits) == 10:
            attempts.append(("phone", lambda: fb_auth.get_user_by_phone_number("+91" + digits)))
    attempts.append(("uid", lambda: fb_auth.get_user(target)))

    for kind, lookup in attempts:
        try:
            return lookup(), kind
        except Exception:
            continue
    return None, None


def _describe(user) -> str:
    bits = [user.uid]
    if user.email:
        bits.append(user.email)
    if user.phone_number:
        bits.append(user.phone_number)
    return "  ".join(bits)


def list_admins() -> int:
    """Every account currently carrying the claim.

    list_users() pages through the whole project, which is fine at this scale
    and is the only way to ask "who is an admin" — custom claims are not
    queryable.
    """
    found = []
    page = fb_auth.list_users()
    while page:
        for user in page.users:
            if (user.custom_claims or {}).get("admin") is True:
                found.append(user)
        page = page.get_next_page()

    if not found:
        print("No accounts currently have admin access.")
        return 0

    print(f"{len(found)} account(s) with admin access:")
    for user in found:
        print(f"  {_describe(user)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", help="uid, email, or phone number")
    parser.add_argument("--apply", action="store_true", help="actually change the claim")
    parser.add_argument("--revoke", action="store_true", help="remove access instead of granting")
    parser.add_argument("--list", action="store_true", help="list accounts that have access")
    args = parser.parse_args()

    init_firebase()

    if args.list:
        return list_admins()

    if not args.target:
        parser.error("a target (uid, email, or phone number) is required unless --list is given")

    user, matched_by = _find_user(args.target)
    if user is None:
        print(f"No account found for {args.target!r}.")
        return 1

    claims = dict(user.custom_claims or {})
    currently = claims.get("admin") is True
    wanted = not args.revoke
    verb = "Revoking" if args.revoke else "Granting"

    print(f"Matched by {matched_by}: {_describe(user)}")
    print(f"  admin claim: {currently} -> {wanted}")

    if currently == wanted:
        print("Nothing to do — the claim is already in that state.")
        return 0

    if not args.apply:
        print(f"\nDry run. Re-run with --apply to actually do it.\n  {verb} access to every shop.")
        return 0

    if wanted:
        claims["admin"] = True
    else:
        claims.pop("admin", None)

    # Passing None rather than {} clears the claims entirely, which is what we
    # want when admin was the only one — it keeps stray empty maps off accounts.
    fb_auth.set_custom_user_claims(user.uid, claims or None)
    print(f"Done. {verb.rstrip('ing')}ed admin access for {user.uid}.")

    if args.revoke:
        # A claim change only reaches the client on the next ID token refresh,
        # which can be up to an hour away. For a revoke that gap matters, so cut
        # the refresh tokens and force a re-authentication now.
        fb_auth.revoke_refresh_tokens(user.uid)
        print("Refresh tokens revoked — the account must sign in again.")
    else:
        print("The account must sign out and back in before the claim is in its token.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
