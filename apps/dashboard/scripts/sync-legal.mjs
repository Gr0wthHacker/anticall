// Copies the acknowledgment text from docs/legal into the app so the Docker build context
// (apps/dashboard only) is self-contained. Run after docs/legal/acknowledgment-v1.json changes.
import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const from = fileURLToPath(new URL("../../../docs/legal/acknowledgment-v1.json", import.meta.url));
const to = fileURLToPath(new URL("../src/lib/acknowledgment-v1.json", import.meta.url));
copyFileSync(from, to);
