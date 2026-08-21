import { NextResponse, type NextRequest } from "next/server";

/**
 * HTTP Basic auth in front of every route.
 *
 * **Why here rather than in Vercel.** Vercel Authentication cannot protect
 * a production URL on the Hobby plan — the API refuses with
 * `invalid_sso_protection` — so `capitalscan.vercel.app` and the branch
 * alias were both publicly readable while only the immutable deployment
 * URL was gated. This closes that at the application layer, where the plan
 * has no say.
 *
 * Middleware runs **before** any page, route handler, or server component,
 * so an unauthenticated request never reaches `lib/db.ts` and never opens a
 * connection. That ordering is the point: authentication that runs after
 * the query has already leaked the expensive part.
 *
 * `/chat` matters most. It spends Anthropic tokens per request, so an open
 * one is someone else's bill as well as your data.
 */

/**
 * Requests allowed through unauthenticated.
 *
 * **Only the build's own immutable assets.** `/_next/static` is
 * content-hashed JavaScript, CSS and fonts — it contains no data, and
 * gating it would break the login page's own styling. `/favicon.ico` is
 * the same argument.
 *
 * Deliberately **not** exempt: `/_next/image`, which proxies arbitrary
 * URLs, and `/api/*`, which is the whole database surface. An exemption
 * list is the usual way this control fails, so this one holds two entries
 * and a test asserts nothing else was added.
 */
const PUBLIC_PREFIXES = ["/_next/static", "/favicon.ico"];

/**
 * Compares two strings without leaking their difference through timing.
 *
 * A `===` on a secret returns as soon as two bytes differ, so the time to
 * reject reveals how many leading characters were right — enough to
 * recover a password byte by byte over many requests. This always walks
 * the full length of both.
 *
 * Length is folded into the result rather than short-circuiting on it,
 * for the same reason.
 */
function safeEqual(a: string, b: string): boolean {
  const length = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < length; i += 1) {
    diff |= (a.codePointAt(i) ?? 0) ^ (b.codePointAt(i) ?? 0);
  }
  return diff === 0;
}

/** The 401 every unauthenticated request gets. Identical in every failure
 * mode — wrong password, missing header, malformed header — so the response
 * says nothing about which. */
function challenge(): NextResponse {
  return new NextResponse("Authentication required.", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="CapitalScan", charset="UTF-8"',
      // A 401 is not a page. Nothing should hold it, least of all a CDN
      // that might then serve it to an authenticated request.
      "cache-control": "no-store",
    },
  });
}

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  if (PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    return NextResponse.next();
  }

  // **An explicit opt-out, deliberately not the absence of a password.**
  // Requested 2026-08-20: the deployment URL is hard to discover and the
  // content is public market data plus the operator's own analysis, so the
  // gate costs more than it protects for now.
  //
  // It is its own variable rather than "unset SITE_PASSWORD means open"
  // because those are different intents and only one of them is safe to
  // infer. Forgetting to set a password is an accident; setting
  // `SITE_AUTH_DISABLED=1` is a decision, and it reads as one in the
  // Vercel dashboard next to the password it overrides.
  //
  // Recorded in `docs/BACKLOG.md`. The thing to re-check before removing
  // this is `/api/chat`: it spends Anthropic tokens per request, and is
  // currently harmless only because it reaches MCP on 127.0.0.1 and fails
  // before any model call. Exposing MCP remotely without restoring this
  // turns an open page into an open wallet.
  if (process.env.SITE_AUTH_DISABLED === "1") return NextResponse.next();

  const expected = process.env.SITE_PASSWORD;

  // **Fails closed.** An unset secret locks the site rather than opening
  // it. The alternative — treating "no password configured" as "no
  // password required" — turns a deployment that forgot the variable into
  // a public one, silently, which is exactly the state this exists to
  // prevent. A local `next dev` with no `.env.local` entry is locked too,
  // and that is the correct inconvenience.
  if (!expected) {
    return new NextResponse(
      "SITE_PASSWORD is not set. This deployment is locked until it is.",
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }

  const header = request.headers.get("authorization") ?? "";
  const [scheme, encoded] = header.split(" ");
  if (scheme?.toLowerCase() !== "basic" || !encoded) return challenge();

  let decoded: string;
  try {
    decoded = atob(encoded);
  } catch {
    // Malformed base64 is a failed attempt, not a server error.
    return challenge();
  }

  // `user:pass`, and the password may itself contain a colon — so split
  // once on the first one rather than on every one.
  const separator = decoded.indexOf(":");
  const password = separator === -1 ? "" : decoded.slice(separator + 1);

  return safeEqual(password, expected) ? NextResponse.next() : challenge();
}

/**
 * Everything, with no route list.
 *
 * A matcher enumerating protected paths is a list that goes stale the day
 * someone adds a route — the new one is unprotected and nothing says so.
 * This matches every request and lets `PUBLIC_PREFIXES` carve out the two
 * exceptions, so a new route is protected by default and opening one is a
 * deliberate edit.
 */
export const config = {
  matcher: "/:path*",
};
