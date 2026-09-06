import { refreshMirror } from "../lib/mirror-watch.js";

/**
 * On-demand mirror check, meant to be called when a real search just failed
 * with code MIRROR_DOWN -- not polled on a timer (see lib/mirror-watch.js
 * for why). Tests the current mirror, and if it's actually dead, walks the
 * other candidates and switches to the first one that's alive.
 */
export default async function handler(req, res) {
  try {
    const result = await refreshMirror();
    res.status(200).json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
