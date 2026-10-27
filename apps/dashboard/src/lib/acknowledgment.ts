import ack from "./acknowledgment-v1.json";

export interface AckItem {
  id: string;
  required: boolean;
  text: string;
}

export const ACKNOWLEDGMENT = ack as {
  version: string;
  status: string;
  notice: string;
  preamble: string;
  items: AckItem[];
};

interface StoredAck {
  version: string;
  accepted_at_utc: string;
  text_sha256: string | null;
}

const key = (userId: string) => `callshield.ack.${userId}`;

export async function hashAckText(): Promise<string | null> {
  try {
    const text = ACKNOWLEDGMENT.items.map((i) => i.text).join("\n");
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

export function readAck(userId: string): StoredAck | null {
  try {
    const raw = window.localStorage.getItem(key(userId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredAck;
    return parsed.version === ACKNOWLEDGMENT.version ? parsed : null;
  } catch {
    return null;
  }
}

/** Returns true if the record was stored. Browser-local: the API has no acknowledgment endpoint. */
export async function saveAck(userId: string): Promise<boolean> {
  const record: StoredAck = {
    version: ACKNOWLEDGMENT.version,
    accepted_at_utc: new Date().toISOString(),
    text_sha256: await hashAckText(),
  };
  try {
    window.localStorage.setItem(key(userId), JSON.stringify(record));
    return true;
  } catch {
    return false;
  }
}
