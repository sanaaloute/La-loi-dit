// RFC 4122 v4 UUID without a crypto dependency. Hermes has no
// crypto.getRandomValues by default; Math.random is sufficient here: the
// values identify a device or a chat session, they are not secrets.
export function uuid(): string {
  const rnd = (n: number): number => Math.floor(Math.random() * n);
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = rnd(16);
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
