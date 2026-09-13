/**
 * Unikum-feed i skyen.
 *
 * Workeren genererer ingenting. Maskinen derhjemme bygger feedet og pusher
 * det hertil, hvor det gemmes i KV og serveres til telefonerne. Dermed
 * behoever hjemmenettet ikke at tage imod indgaaende trafik, og skyen
 * kender hverken BankID-sessionen eller Unikum.
 *
 * To noegler med hvert sit formaal:
 *   FEED_TOKEN  laeseadgang  - staar i URL'en, som RSS-apps og ESP32'en kan
 *   PUSH_TOKEN  skriveadgang - kun paa maskinen derhjemme
 */

const ASSETS = {
  "feed.xml": "application/rss+xml; charset=utf-8",
  "display.json": "application/json; charset=utf-8",
};

/** Sammenligning uden at svartiden roeber, hvor langt et gaet naaede. */
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const ab = new TextEncoder().encode(a);
  const bb = new TextEncoder().encode(b);
  if (ab.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < ab.length; i++) diff |= ab[i] ^ bb[i];
  return diff === 0;
}

/**
 * Sammenlign If-None-Match med vores ETag.
 *
 * Cloudflare komprimerer svaret og saetter derfor "W/" foran vores ETag,
 * naar den naar klienten. Klienten sender den svage form tilbage, saa en
 * ordret sammenligning rammer aldrig - og telefonen henter hele feedet hver
 * gang i stedet for at faa 304. Vi sammenligner derfor uden praefikset.
 * Headeren kan desuden indeholde flere vaerdier adskilt af komma, og "*"
 * matcher alt.
 */
function matchesEtag(header, etag) {
  if (!header) return false;
  const strip = (v) => v.trim().replace(/^W\//, "");
  const ours = strip(etag);
  return header.split(",").some((v) => {
    const candidate = strip(v);
    return candidate === "*" || candidate === ours;
  });
}

function bearer(request) {
  const header = request.headers.get("Authorization") || "";
  return header.startsWith("Bearer ") ? header.slice(7) : "";
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const name = url.pathname.replace(/^\/+/, "") || "feed.xml";

    if (!Object.prototype.hasOwnProperty.call(ASSETS, name)) {
      return new Response("Ikke fundet\n", { status: 404 });
    }

    // --- Push fra maskinen derhjemme ---------------------------------
    if (request.method === "PUT") {
      if (!env.PUSH_TOKEN || !safeEqual(bearer(request), env.PUSH_TOKEN)) {
        return new Response("Ingen skriveadgang\n", { status: 403 });
      }
      const body = await request.text();
      if (!body) return new Response("Tomt indhold\n", { status: 400 });

      // Hashen kommer fra afsenderen, saa vi slipper for at beregne den
      // ved hver eneste laesning. Den bliver til ETag nedenfor.
      await env.FEED.put(name, body, {
        metadata: {
          hash: request.headers.get("X-Content-Hash") || "",
          updated: new Date().toISOString(),
        },
      });
      return new Response(`Gemt ${name} (${body.length} tegn)\n`, { status: 200 });
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Metoden er ikke tilladt\n", { status: 405 });
    }

    // --- Laesning fra telefon, RSS-app eller ESP32 --------------------
    const token = url.searchParams.get("token") || "";
    if (!env.FEED_TOKEN || !safeEqual(token, env.FEED_TOKEN)) {
      return new Response("Forkert token\n", { status: 403 });
    }

    const { value, metadata } = await env.FEED.getWithMetadata(name);
    if (value === null) {
      return new Response("Feedet er ikke publiceret endnu\n", { status: 404 });
    }

    const etag = metadata && metadata.hash ? `"${metadata.hash}"` : null;
    // RSS-laesere henter tit. Svarer vi 304, naar intet er aendret, sparer
    // vi baade data og batteri paa telefonen.
    if (etag && matchesEtag(request.headers.get("If-None-Match"), etag)) {
      return new Response(null, { status: 304, headers: { ETag: etag } });
    }

    const headers = {
      "Content-Type": ASSETS[name],
      "Cache-Control": "private, max-age=300",
      "X-Robots-Tag": "noindex, nofollow",
    };
    if (etag) headers.ETag = etag;
    if (metadata && metadata.updated) headers["Last-Modified-ISO"] = metadata.updated;

    return new Response(request.method === "HEAD" ? null : value, { headers });
  },
};
