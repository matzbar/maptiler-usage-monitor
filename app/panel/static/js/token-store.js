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

// The chosen project: a per-browser convenience in localStorage, so reopening the panel keeps the choice.
// Not a secret (a project name). Unavailable storage just means the panel opens on the whole account.

const PROJECT_KEY = "maptiler-monitor-project";

export function createProjectStore(storage = globalThis.localStorage) {
  return {
    get() {
      try {
        return storage?.getItem(PROJECT_KEY) ?? "";
      } catch {
        return "";
      }
    },
    set(name) {
      try {
        if (name) storage?.setItem(PROJECT_KEY, name);
        else storage?.removeItem(PROJECT_KEY);
      } catch {
        // storage unavailable: the choice lasts until the page is reloaded
      }
    },
  };
}
