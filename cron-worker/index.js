// Starts the release workflow; it builds only when craft-launcher has a release this repo doesn't.
const DISPATCH_URL =
  "https://api.github.com/repos/wdonega/craft-launcher-web/actions/workflows/release.yml/dispatches";

export default {
  async scheduled(_event, env) {
    const res = await fetch(DISPATCH_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "craft-launcher-web-cron",
      },
      body: JSON.stringify({ ref: "main", inputs: { auto: "true" } }),
    });
    if (!res.ok) throw new Error(`GitHub ${res.status}: ${await res.text()}`);
  },
};
