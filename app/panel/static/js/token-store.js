// Where the admin token lives: sessionStorage (cleared when the tab closes).
// Falls back to memory when storage is unavailable (private mode, blocked site data).

const STORAGE_KEY = "maptiler-monitor-admin-token";

export function createTokenStore(storage = globalThis.sessionStorage) {
  let memoryToken = "";

  return {
    get() {
      try {
        return storage?.getItem(STORAGE_KEY) ?? memoryToken;
      } catch {
        return memoryToken;
      }
    },
    set(token) {
      memoryToken = token;
      try {
        storage?.setItem(STORAGE_KEY, token);
      } catch {
        // storage unavailable: the in-memory copy is enough for this page load
      }
    },
    clear() {
      memoryToken = "";
      try {
        storage?.removeItem(STORAGE_KEY);
      } catch {
        // nothing stored
      }
    },
  };
}
