import worker from "./src/index.js";

const store = new Map();
const env = {
  FEED_TOKEN: "laese-hemmelighed",
  PUSH_TOKEN: "skrive-hemmelighed",
  FEED: {
    async put(k, v, opts) { store.set(k, { value: v, metadata: opts?.metadata ?? null }); },
    async getWithMetadata(k) {
      const e = store.get(k);
      return e ? { value: e.value, metadata: e.metadata } : { value: null, metadata: null };
    },
  },
};

const call = (method, path, { token, bearer, body, headers = {} } = {}) => {
  const url = new URL("https://x.workers.dev" + path);
  if (token !== undefined) url.searchParams.set("token", token);
  const h = { ...headers };
  if (bearer) h.Authorization = `Bearer ${bearer}`;
  return worker.fetch(new Request(url, { method, body, headers: h }), env);
};

let failed = 0;
const check = (navn, faktisk, forventet) => {
  const ok = faktisk === forventet;
  if (!ok) failed++;
  console.log(`${ok ? "  ok  " : "  FEJL"} ${navn}: ${faktisk}${ok ? "" : ` (forventede ${forventet})`}`);
};

console.log("Foer publicering:");
check("GET uden token", (await call("GET", "/feed.xml")).status, 403);
check("GET forkert token", (await call("GET", "/feed.xml", { token: "gaet" })).status, 403);
check("GET rigtig token, intet indhold", (await call("GET", "/feed.xml", { token: env.FEED_TOKEN })).status, 404);
check("ukendt sti", (await call("GET", "/hemmeligt.txt", { token: env.FEED_TOKEN })).status, 404);
check("PUT uden noegle", (await call("PUT", "/feed.xml", { body: "<rss/>" })).status, 403);
check("PUT med laesenoeglen", (await call("PUT", "/feed.xml", { body: "<rss/>", bearer: env.FEED_TOKEN })).status, 403);
check("PUT tomt indhold", (await call("PUT", "/feed.xml", { body: "", bearer: env.PUSH_TOKEN })).status, 400);

console.log("\nPublicering:");
check("PUT med skrivenoegle", (await call("PUT", "/feed.xml", {
  body: "<rss>hej</rss>", bearer: env.PUSH_TOKEN, headers: { "X-Content-Hash": "abc123" },
})).status, 200);

const r = await call("GET", "/feed.xml", { token: env.FEED_TOKEN });
check("GET efter publicering", r.status, 200);
check("indhold", await r.text(), "<rss>hej</rss>");
check("content-type", r.headers.get("Content-Type"), "application/rss+xml; charset=utf-8");
check("etag", r.headers.get("ETag"), '"abc123"');
check("ikke indekseret", r.headers.get("X-Robots-Tag"), "noindex, nofollow");

console.log("\nBetinget hentning:");
check("uaendret giver 304", (await call("GET", "/feed.xml", {
  token: env.FEED_TOKEN, headers: { "If-None-Match": '"abc123"' },
})).status, 304);
check("aendret giver 200", (await call("GET", "/feed.xml", {
  token: env.FEED_TOKEN, headers: { "If-None-Match": '"gammel"' },
})).status, 200);

console.log("\nOevrigt:");
check("HEAD virker", (await call("HEAD", "/feed.xml", { token: env.FEED_TOKEN })).status, 200);
check("HEAD har tom krop", (await (await call("HEAD", "/feed.xml", { token: env.FEED_TOKEN })).text()), "");
check("DELETE afvises", (await call("DELETE", "/feed.xml", { token: env.FEED_TOKEN })).status, 405);
check("rod giver feedet", (await call("GET", "/", { token: env.FEED_TOKEN })).status, 200);

console.log(failed === 0 ? "\nAlle test bestaaet." : `\n${failed} TEST FEJLEDE.`);
process.exit(failed === 0 ? 0 : 1);
