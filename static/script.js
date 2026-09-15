const refreshBtn = document.getElementById("refreshBtn");
const totalStakeInput = document.getElementById("totalStake");
const arbListEl = document.getElementById("arbList");
const statusEl = document.getElementById("status");
const booksPanelEl = document.getElementById("booksPanel");
const toggleAllBtn = document.getElementById("toggleAllBtn");
const autoRefreshCheckbox = document.getElementById("autoRefresh");
const sportsPanelEl = document.getElementById("sportsPanel");
const toggleAllSportsBtn = document.getElementById("toggleAllSportsBtn");
const leagueRowEl = document.getElementById("leagueRow");
const leagueSelectEl = document.getElementById("leagueSelect");
const sportLeagueNoteEl = document.getElementById("sportLeagueNote");
const scanScopeNoteEl = document.getElementById("scanScopeNote");
const liveFilterSelect = document.getElementById("liveFilter");
const minProfitFilterInput = document.getElementById("minProfitFilter");
const sortBySelect = document.getElementById("sortBy");
const filtersToggleBtn = document.getElementById("filtersToggleBtn");
const filtersPanelEl = document.getElementById("filtersPanel");
const filtersSummaryEl = document.getElementById("filtersSummary");
const sportsToggleBtn = document.getElementById("sportsToggleBtn");
const sportsSectionSummaryEl = document.getElementById("sportsSectionSummary");
const booksToggleBtn = document.getElementById("booksToggleBtn");
const booksSectionSummaryEl = document.getElementById("booksSectionSummary");
const booksSectionBodyEl = document.getElementById("booksSectionBody");
const addBookForm = document.getElementById("addBookForm");
const addBookInput = document.getElementById("addBookInput");
const sportsFavoritesOnlyToggle = document.getElementById("sportsFavoritesOnlyToggle");
const booksFavoritesOnlyToggle = document.getElementById("booksFavoritesOnlyToggle");

const FILTERS_COLLAPSED_KEY = "arbScreenerFiltersCollapsed";
const SPORTS_SECTION_COLLAPSED_KEY = "arbScreenerSportsSectionCollapsed";
const BOOKS_SECTION_COLLAPSED_KEY = "arbScreenerBooksSectionCollapsed";
const CUSTOM_BOOKS_KEY = "arbScreenerCustomBooks";
const SELECTED_BOOK_IDS_KEY = "arbScreenerSelectedBookIds";
// "My List" - a personal shortlist of sports/books you actually use, so the
// panel can be narrowed down to just those (still individually toggleable
// within it) instead of scrolling the full catalog every time. Useful when
// your SharpAPI plan caps how many books can be selected at once (e.g.
// Hobby's 5-book cap) but you rotate between a wider set of ~10 you follow.
const FAVORITE_SPORT_IDS_KEY = "arbScreenerFavoriteSportIds";
const FAVORITE_BOOK_IDS_KEY = "arbScreenerFavoriteBookIds";
const SPORTS_FAVORITES_ONLY_KEY = "arbScreenerSportsFavoritesOnly";
const BOOKS_FAVORITES_ONLY_KEY = "arbScreenerBooksFavoritesOnly";

// Confirmed rate limit for the Hobby plan (per SharpAPI's own docs):
// 120 requests/minute. Duplicated from app.py's SHARPAPI_RATE_LIMIT_PER_MINUTE
// (no shared config between backend/frontend in this app) - keep in sync
// if the plan tier ever changes.
const SHARPAPI_RATE_LIMIT_PER_MINUTE = 120;

// Auto-refresh interval scales with how many requests one scan actually
// costs (sports x books, with a pagination fudge factor - see
// computeAutoRefreshIntervalMs), instead of the previous fixed 15s that
// also got HARD-DISABLED entirely whenever 2+ sports were selected. That
// binary cutoff is why auto-refresh looked "broken" by default: this
// site's own preferred-sports default preselects 6 sports simultaneously,
// so auto-refresh was silently off from the very first page load unless
// you happened to narrow down to exactly one sport. Scaling the interval
// instead means it always runs, just paced to stay within budget - a
// heavier scan just refreshes less often rather than not at all.
const MIN_AUTO_REFRESH_INTERVAL_MS = 8000; // matches SharpAPI's own suggested 5-10s cadence for live dashboards
const AUTO_REFRESH_BUDGET_FRACTION = 0.5;  // use at most half the rate limit for auto-refresh, leaving room for manual Refresh clicks and other tabs
const PAGINATION_FUDGE_FACTOR = 1.5;       // a busy slate can trigger multiple pages/book (see MAX_ODDS_PAGES_PER_BOOK in app.py) - pad the estimate rather than undercount
const PREFERRED_DEFAULT_LEAGUE = "nfl";

function computeAutoRefreshIntervalMs() {
  const sportCount = Math.max(selectedSportIds.size, 1);
  const bookCount = Math.max(selectedBookIds.size, 1);
  const estimatedRequestsPerScan = sportCount * bookCount * PAGINATION_FUDGE_FACTOR;
  const maxRequestsPerMinute = SHARPAPI_RATE_LIMIT_PER_MINUTE * AUTO_REFRESH_BUDGET_FRACTION;
  const minIntervalForBudgetMs = (estimatedRequestsPerScan / maxRequestsPerMinute) * 60000;
  return Math.max(MIN_AUTO_REFRESH_INTERVAL_MS, minIntervalForBudgetMs);
}

let currentArbs = [];
let allBooks = [];          // [{id, display_name}, ...]
let selectedBookIds = new Set();
let allSports = [];         // [{id, name}, ...]
let selectedSportIds = new Set();
let currentLeague = "all";  // only meaningful when exactly one sport is selected
let isLoading = false;
let autoRefreshTimer = null;
let favoriteSportIds = loadIdSetFromStorage(FAVORITE_SPORT_IDS_KEY);
let favoriteBookIds = loadIdSetFromStorage(FAVORITE_BOOK_IDS_KEY);
let sportsFavoritesOnly = loadFlagFromStorage(SPORTS_FAVORITES_ONLY_KEY);
let booksFavoritesOnly = loadFlagFromStorage(BOOKS_FAVORITES_ONLY_KEY);

function showSportLeagueNote(text) {
  if (!text) {
    sportLeagueNoteEl.hidden = true;
    return;
  }
  sportLeagueNoteEl.textContent = text;
  sportLeagueNoteEl.hidden = false;
}

// Kept up to date on every sport/book/live-filter change so the summary is
// still useful when the panel is collapsed, not just when it's open.
function updateFiltersSummary() {
  const sportCount = selectedSportIds.size;
  const bookCount = selectedBookIds.size;
  const liveOption = liveFilterSelect.options[liveFilterSelect.selectedIndex];
  const liveLabel = liveOption ? liveOption.textContent : "All events";
  filtersSummaryEl.textContent =
    `${sportCount} sport${sportCount === 1 ? "" : "s"} · ${bookCount} book${bookCount === 1 ? "" : "s"} · ${liveLabel}`;
}

function setFiltersCollapsed(collapsed) {
  filtersPanelEl.hidden = collapsed;
  filtersToggleBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  try {
    localStorage.setItem(FILTERS_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch (err) {
    // private browsing / blocked storage - fine to just not persist
  }
}

filtersToggleBtn.addEventListener("click", () => {
  setFiltersCollapsed(!filtersPanelEl.hidden);
});

// Sports and Sportsbooks are independently collapsible sub-sections of the
// Filters panel, so either can be closed while working on the other
// instead of both always being open together.
function setSectionCollapsed(toggleBtn, panelEl, storageKey, collapsed) {
  panelEl.hidden = collapsed;
  toggleBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  try {
    localStorage.setItem(storageKey, collapsed ? "1" : "0");
  } catch (err) {
    // private browsing / blocked storage - fine to just not persist
  }
}

sportsToggleBtn.addEventListener("click", () => {
  setSectionCollapsed(sportsToggleBtn, sportsPanelEl, SPORTS_SECTION_COLLAPSED_KEY, !sportsPanelEl.hidden);
});

booksToggleBtn.addEventListener("click", () => {
  setSectionCollapsed(booksToggleBtn, booksSectionBodyEl, BOOKS_SECTION_COLLAPSED_KEY, !booksSectionBodyEl.hidden);
});

function updateSportsSectionSummary() {
  const count = selectedSportIds.size;
  sportsSectionSummaryEl.textContent = `${count} selected`;
}

function updateBooksSectionSummary() {
  const count = selectedBookIds.size;
  booksSectionSummaryEl.textContent = `${count} selected`;
}

// Light normalization for the id actually sent to SharpAPI as the
// "sportsbook" param - just trims whitespace and lowercases, so it
// preserves whatever exact id the user typed (SharpAPI's own id format
// isn't confirmed beyond the 5 plan-configured books).
function toBookId(raw) {
  return raw.trim().toLowerCase().replace(/\s+/g, "");
}

// Stricter normalization used only to detect duplicates (matches the
// backend's _normalize_book), so "Bet Rivers" typed into "Add a
// sportsbook" is recognized as the same book as the existing "betrivers".
function normalizeBookKey(id) {
  return (id || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function loadCustomBooksFromStorage() {
  try {
    const raw = localStorage.getItem(CUSTOM_BOOKS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (err) {
    return [];
  }
}

function saveCustomBooksToStorage() {
  try {
    const custom = allBooks.filter((b) => b.custom);
    localStorage.setItem(CUSTOM_BOOKS_KEY, JSON.stringify(custom));
  } catch (err) {
    // private browsing / blocked storage - fine to just not persist
  }
}

function loadSelectedBookIdsFromStorage() {
  try {
    const raw = localStorage.getItem(SELECTED_BOOK_IDS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    return null;
  }
}

function saveSelectedBookIdsToStorage() {
  try {
    localStorage.setItem(SELECTED_BOOK_IDS_KEY, JSON.stringify([...selectedBookIds]));
  } catch (err) {
    // private browsing / blocked storage - fine to just not persist
  }
}

function loadIdSetFromStorage(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch (err) {
    return new Set();
  }
}

function saveIdSetToStorage(key, idSet) {
  try {
    localStorage.setItem(key, JSON.stringify([...idSet]));
  } catch (err) {
    // private browsing / blocked storage - fine to just not persist
  }
}

function loadFlagFromStorage(key) {
  try {
    return localStorage.getItem(key) === "1";
  } catch (err) {
    return false;
  }
}

function saveFlagToStorage(key, value) {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch (err) {
    // private browsing / blocked storage - fine to just not persist
  }
}

async function loadSports() {
  try {
    const res = await fetch("/api/sports");
    const data = await res.json();
    allSports = data.sports || [];
    const preferredIds = new Set(data.preferred || []);
    const availableIds = new Set(allSports.map((s) => s.id));
    const preferredAvailable = [...preferredIds].filter((id) => availableIds.has(id));

    // Preselect the preferred sports (basketball/football/hockey/soccer/
    // tennis) wherever they actually exist in the real list; if none of
    // them matched at all, fall back to just the first sport so something
    // is always selected rather than nothing.
    selectedSportIds = new Set(
      preferredAvailable.length ? preferredAvailable : (allSports[0] ? [allSports[0].id] : [])
    );

    if (data.source === "error") {
      showSportLeagueNote(`Couldn't reach SharpAPI's sports list (${data.error}) — showing a short fallback list instead.`);
    }

    renderSportsPanel();
  } catch (err) {
    console.error("Failed to load sports:", err);
    showSportLeagueNote("Couldn't load the sports list. Try refreshing the page.");
  }
}

// The "My List" toggle (sportsFavoritesOnly) narrows this down to just the
// starred sports instead of the full catalog - Select All and the empty-list
// hint below both need this same filtered view, so it's shared.
function getVisibleSports() {
  const sorted = [...allSports].sort((a, b) => a.name.localeCompare(b.name));
  return sportsFavoritesOnly ? sorted.filter((s) => favoriteSportIds.has(s.id)) : sorted;
}

function renderSportsPanel() {
  if (!allSports.length) {
    sportsPanelEl.innerHTML = "";
    toggleAllSportsBtn.style.display = "none";
    return;
  }
  toggleAllSportsBtn.style.display = "inline-block";

  const visibleSports = getVisibleSports();

  if (sportsFavoritesOnly && !visibleSports.length) {
    sportsPanelEl.innerHTML = `<div class="favorites-empty-hint">No sports in My List yet — click ☆ on any sport below to add it, or turn off "My List" to see all sports.</div>`;
  } else {
    sportsPanelEl.innerHTML = visibleSports
      .map((s) => {
        const checked = selectedSportIds.has(s.id) ? "checked" : "";
        const isFav = favoriteSportIds.has(s.id);
        return `
          <span class="toggle-item">
            <label class="book-toggle">
              <input type="checkbox" data-sport-id="${s.id}" ${checked} />
              ${s.name}
            </label>
            <button type="button" class="fav-star-btn${isFav ? " is-favorite" : ""}" data-fav-sport-id="${s.id}" title="${isFav ? "Remove from My List" : "Add to My List"}" aria-label="${isFav ? "Remove from My List" : "Add to My List"}">${isFav ? "★" : "☆"}</button>
          </span>
        `;
      })
      .join("");
  }

  sportsPanelEl.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", async (e) => {
      const id = e.target.getAttribute("data-sport-id");
      if (e.target.checked) {
        selectedSportIds.add(id);
      } else {
        selectedSportIds.delete(id);
      }
      updateToggleAllSportsLabel();
      updateFiltersSummary();
      updateSportsSectionSummary();
      await onSportSelectionChanged();
    });
  });

  sportsPanelEl.querySelectorAll("button[data-fav-sport-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-fav-sport-id");
      if (favoriteSportIds.has(id)) {
        favoriteSportIds.delete(id);
      } else {
        favoriteSportIds.add(id);
      }
      saveIdSetToStorage(FAVORITE_SPORT_IDS_KEY, favoriteSportIds);
      renderSportsPanel();
    });
  });

  updateToggleAllSportsLabel();
  updateSportsSectionSummary();
}

function updateToggleAllSportsLabel() {
  const visible = getVisibleSports();
  const allSelected = visible.length > 0 && visible.every((s) => selectedSportIds.has(s.id));
  toggleAllSportsBtn.textContent = allSelected ? "Deselect All" : "Select All";
}

// Select All/Deselect All only acts on the currently visible sports, so
// with "My List" on it toggles just your starred sports rather than
// pulling in the full catalog.
toggleAllSportsBtn.addEventListener("click", async () => {
  const visible = getVisibleSports();
  const allSelected = visible.length > 0 && visible.every((s) => selectedSportIds.has(s.id));
  if (allSelected) {
    visible.forEach((s) => selectedSportIds.delete(s.id));
  } else {
    visible.forEach((s) => selectedSportIds.add(s.id));
  }
  renderSportsPanel();
  updateFiltersSummary();
  updateSportsSectionSummary();
  await onSportSelectionChanged();
});

sportsFavoritesOnlyToggle.addEventListener("change", () => {
  sportsFavoritesOnly = sportsFavoritesOnlyToggle.checked;
  saveFlagToStorage(SPORTS_FAVORITES_ONLY_KEY, sportsFavoritesOnly);
  renderSportsPanel();
});

// "All Leagues" is always offered, even if the specific-leagues fetch below
// fails or comes back empty — it just omits the league filter server-side,
// so it doesn't depend on knowing real league ids in advance.
async function loadLeagues(sport) {
  leagueSelectEl.innerHTML = `<option value="">Loading leagues…</option>`;
  try {
    const res = await fetch(`/api/leagues?sport=${encodeURIComponent(sport)}`);
    const data = await res.json();
    const leagues = data.leagues || [];
    const options = [{ id: "all", name: "All Leagues" }, ...leagues];
    leagueSelectEl.innerHTML = options.map((l) => `<option value="${l.id}">${l.name}</option>`).join("");

    if (!leagues.length) {
      currentLeague = "all";
      leagueSelectEl.value = "all";
      showSportLeagueNote(
        data.source === "error"
          ? `Couldn't reach SharpAPI's leagues list (${data.error}) — you can still pick "All Leagues" to scan broadly.`
          : 'SharpAPI didn\'t return any specific leagues for this sport — you can still pick "All Leagues" to scan broadly.'
      );
      return;
    }

    if (data.source === "error") {
      showSportLeagueNote(`Couldn't reach SharpAPI's leagues list (${data.error}) — showing a fallback instead.`);
    } else {
      showSportLeagueNote(null);
    }

    const preferred = leagues.find((l) => l.id === PREFERRED_DEFAULT_LEAGUE);
    currentLeague = preferred ? preferred.id : leagues[0].id;
    leagueSelectEl.value = currentLeague;
  } catch (err) {
    console.error("Failed to load leagues:", err);
    leagueSelectEl.innerHTML = `<option value="all">All Leagues</option>`;
    currentLeague = "all";
    showSportLeagueNote('Couldn\'t load the specific leagues list — you can still pick "All Leagues" to scan broadly.');
  }
}

// Called whenever the set of checked sports changes. The League dropdown
// only makes sense when exactly one sport is picked (a league belongs to
// one sport, not several), so it's hidden the rest of the time and the
// scan implicitly covers every league within whichever sport(s) are checked.
async function onSportSelectionChanged() {
  if (selectedSportIds.size === 1) {
    const onlySport = [...selectedSportIds][0];
    leagueRowEl.hidden = false;
    await loadLeagues(onlySport);
  } else {
    leagueRowEl.hidden = true;
    currentLeague = "all";
    showSportLeagueNote(null);
  }
  updateScanScopeState();
  loadArbs();
}

function updateScanScopeState() {
  // Auto-refresh is never hard-disabled anymore - it used to force itself
  // off whenever 2+ sports were selected, which (combined with this site's
  // own default of 6 preselected sports) meant it was silently off from
  // the very first page load for most people. Instead the interval scales
  // with load (see computeAutoRefreshIntervalMs) and always keeps running.
  const heavy = selectedSportIds.size > 1;
  if (heavy) {
    const intervalSeconds = Math.round(computeAutoRefreshIntervalMs() / 1000);
    scanScopeNoteEl.textContent = `Scanning ${selectedSportIds.size} sports means one API request per sport per book selected — with your 120 requests/minute limit, auto-refresh paces itself to about every ${intervalSeconds}s while this many sports are selected, instead of the usual ${Math.round(MIN_AUTO_REFRESH_INTERVAL_MS / 1000)}s, so it stays within budget. Narrow to fewer sports for faster auto-refresh.`;
    scanScopeNoteEl.hidden = false;
  } else {
    scanScopeNoteEl.hidden = true;
  }
  restartAutoRefreshIfRunning();
}

async function loadBooks() {
  try {
    const res = await fetch("/api/books");
    const data = await res.json();
    const serverBooks = (data.books || []).map((b) => ({ ...b, custom: false }));
    const serverKeys = new Set(serverBooks.map((b) => normalizeBookKey(b.id)));

    // Custom books added via the UI in a previous visit, minus any that
    // now collide with a server-provided id (e.g. it got added to
    // SHARPAPI_BOOKS/CANDIDATE_SPORTSBOOKS since).
    const customBooks = loadCustomBooksFromStorage().filter(
      (b) => !serverKeys.has(normalizeBookKey(b.id))
    );

    allBooks = [...serverBooks, ...customBooks];

    const savedSelection = loadSelectedBookIdsFromStorage();
    if (savedSelection) {
      const knownIds = new Set(allBooks.map((b) => b.id));
      selectedBookIds = new Set(savedSelection.filter((id) => knownIds.has(id)));
      if (selectedBookIds.size === 0) {
        // Nothing from the saved selection still exists (e.g. very stale
        // storage) - fall back to the default rather than scanning nothing.
        selectedBookIds = new Set(allBooks.filter((b) => b.preselected).map((b) => b.id));
      }
    } else {
      selectedBookIds = new Set(allBooks.filter((b) => b.preselected).map((b) => b.id));
    }

    renderBooksPanel();
  } catch (err) {
    console.error("Failed to load books:", err);
  }
}

const TIER_LABELS = { free: "Free", hobby: "Hobby", pro: "Pro", sharp: "Sharp" };

// SharpAPI's confirmed error codes for a book that contributed nothing -
// tier_restricted (your plan tier doesn't cover it) is a different problem
// than book_not_selected (your tier DOES cover it, but it isn't turned on
// in your SharpAPI dashboard's own book selection) - worth telling apart
// since only one of those is fixable by a toggle in your SharpAPI account,
// not this site.
const BOOK_ISSUE_LABELS = {
  tier_restricted: "needs a higher SharpAPI plan tier",
  book_not_selected: "not enabled in your SharpAPI dashboard's book selection",
  book_unavailable: "temporarily unavailable on SharpAPI's side",
  rate_limited: "rate limited — try again shortly",
  request_failed: "unreachable",
};

function describeBookIssues(bookIssues) {
  if (!bookIssues || !Object.keys(bookIssues).length) return "";
  const parts = Object.entries(bookIssues).map(([id, code]) => {
    const known = allBooks.find((b) => normalizeBookKey(b.id) === normalizeBookKey(id));
    const label = known ? known.display_name : id;
    const reason = BOOK_ISSUE_LABELS[code] || code;
    return `${label} (${reason})`;
  });
  return ` · Skipped: ${parts.join(", ")}`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

// Same "My List" narrowing as getVisibleSports(), for books.
function getVisibleBooks() {
  const sorted = [...allBooks].sort((a, b) => a.display_name.localeCompare(b.display_name));
  return booksFavoritesOnly ? sorted.filter((b) => favoriteBookIds.has(b.id)) : sorted;
}

function renderBooksPanel() {
  if (!allBooks.length) {
    booksPanelEl.innerHTML = "";
    toggleAllBtn.style.display = "none";
    return;
  }
  toggleAllBtn.style.display = "inline-block";

  const visibleBooks = getVisibleBooks();

  if (booksFavoritesOnly && !visibleBooks.length) {
    booksPanelEl.innerHTML = `<div class="favorites-empty-hint">No sportsbooks in My List yet — click ☆ on any book below to add it, or turn off "My List" to see all books.</div>`;
  } else {
    booksPanelEl.innerHTML = visibleBooks
      .map((b) => {
        const checked = selectedBookIds.has(b.id) ? "checked" : "";
        const name = escapeHtml(b.display_name);
        // Lets you see at a glance why a toggled-on book might return
        // nothing - it needs a higher SharpAPI plan tier than you're on.
        const tierLabel = b.tier ? TIER_LABELS[b.tier] || b.tier : null;
        const labelTitle = tierLabel ? ` title="Requires ${escapeHtml(tierLabel)} tier or higher on SharpAPI"` : "";
        const isFav = favoriteBookIds.has(b.id);
        // The remove (x) and star buttons are siblings of the label, not
        // nested inside it - nesting a button inside a <label> wrapping a
        // checkbox causes browsers to double-toggle the checkbox when the
        // button is clicked. They're wrapped together in .toggle-item so
        // they wrap to the next line as one unit instead of splitting apart.
        const removeBtn = b.custom
          ? `<button type="button" class="book-remove-btn" data-remove-book-id="${escapeHtml(b.id)}" title="Remove ${name}" aria-label="Remove ${name}">&times;</button>`
          : "";
        return `
          <span class="toggle-item">
            <label class="book-toggle"${labelTitle}>
              <input type="checkbox" data-book-id="${escapeHtml(b.id)}" ${checked} />
              ${name}${tierLabel ? `<span class="book-tier-badge">${escapeHtml(tierLabel)}</span>` : ""}
            </label>
            <button type="button" class="fav-star-btn${isFav ? " is-favorite" : ""}" data-fav-book-id="${escapeHtml(b.id)}" title="${isFav ? "Remove from My List" : "Add to My List"}" aria-label="${isFav ? "Remove from My List" : "Add to My List"}">${isFav ? "★" : "☆"}</button>
            ${removeBtn}
          </span>
        `;
      })
      .join("");
  }

  booksPanelEl.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", (e) => {
      const id = e.target.getAttribute("data-book-id");
      if (e.target.checked) {
        selectedBookIds.add(id);
      } else {
        selectedBookIds.delete(id);
      }
      updateToggleAllLabel();
      updateFiltersSummary();
      updateBooksSectionSummary();
      saveSelectedBookIdsToStorage();
      restartAutoRefreshIfRunning(); // book count affects the paced interval
      loadArbs(); // re-scan immediately with the new book selection
    });
  });

  booksPanelEl.querySelectorAll("button[data-remove-book-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-remove-book-id");
      allBooks = allBooks.filter((b) => b.id !== id);
      selectedBookIds.delete(id);
      favoriteBookIds.delete(id);
      saveIdSetToStorage(FAVORITE_BOOK_IDS_KEY, favoriteBookIds);
      saveCustomBooksToStorage();
      saveSelectedBookIdsToStorage();
      renderBooksPanel();
      updateFiltersSummary();
      updateBooksSectionSummary();
      restartAutoRefreshIfRunning();
      loadArbs();
    });
  });

  booksPanelEl.querySelectorAll("button[data-fav-book-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-fav-book-id");
      if (favoriteBookIds.has(id)) {
        favoriteBookIds.delete(id);
      } else {
        favoriteBookIds.add(id);
      }
      saveIdSetToStorage(FAVORITE_BOOK_IDS_KEY, favoriteBookIds);
      renderBooksPanel();
    });
  });

  updateToggleAllLabel();
  updateBooksSectionSummary();
}

function updateToggleAllLabel() {
  const visible = getVisibleBooks();
  const allSelected = visible.length > 0 && visible.every((b) => selectedBookIds.has(b.id));
  toggleAllBtn.textContent = allSelected ? "Deselect All" : "Select All";
}

// Select All/Deselect All only acts on the currently visible books, so with
// "My List" on it toggles just your starred books rather than pulling in
// the full catalog (useful when your plan caps simultaneous book selection
// below the size of your full My List).
toggleAllBtn.addEventListener("click", () => {
  const visible = getVisibleBooks();
  const allSelected = visible.length > 0 && visible.every((b) => selectedBookIds.has(b.id));
  if (allSelected) {
    visible.forEach((b) => selectedBookIds.delete(b.id));
  } else {
    visible.forEach((b) => selectedBookIds.add(b.id));
  }
  renderBooksPanel();
  updateFiltersSummary();
  saveSelectedBookIdsToStorage();
  restartAutoRefreshIfRunning();
  loadArbs();
});

booksFavoritesOnlyToggle.addEventListener("change", () => {
  booksFavoritesOnly = booksFavoritesOnlyToggle.checked;
  saveFlagToStorage(BOOKS_FAVORITES_ONLY_KEY, booksFavoritesOnly);
  renderBooksPanel();
});

// Lets the user add any exact sportsbook id from their own SharpAPI
// dashboard that isn't already offered as a toggle - covers anything
// missing from CANDIDATE_SPORTSBOOKS (an unconfirmed, best-effort list)
// without needing a code change or redeploy.
addBookForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const rawInput = addBookInput.value.trim();
  if (!rawInput) return;

  const id = toBookId(rawInput);
  const key = normalizeBookKey(id);
  if (!id || !key) return;

  if (allBooks.some((b) => normalizeBookKey(b.id) === key)) {
    addBookInput.value = "";
    addBookInput.placeholder = "Already in the list above";
    return;
  }

  const displayName = rawInput
    .trim()
    .split(/\s+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

  allBooks.push({ id, display_name: displayName, preselected: false, custom: true });
  selectedBookIds.add(id);
  addBookInput.value = "";

  saveCustomBooksToStorage();
  saveSelectedBookIdsToStorage();
  renderBooksPanel();
  updateFiltersSummary();
  restartAutoRefreshIfRunning();
  loadArbs();
});

async function loadArbs() {
  if (isLoading) return; // don't stack overlapping requests (manual click + auto-refresh + toggle spam)

  if (selectedSportIds.size === 0) {
    statusEl.textContent = "No sports selected — toggle at least one on to scan.";
    statusEl.classList.remove("is-loading");
    currentArbs = [];
    renderArbs();
    return;
  }

  if (selectedBookIds.size === 0) {
    statusEl.textContent = "No sportsbooks selected — toggle at least one on to scan.";
    statusEl.classList.remove("is-loading");
    currentArbs = [];
    renderArbs();
    return;
  }

  isLoading = true;
  refreshBtn.disabled = true;
  statusEl.classList.add("is-loading");
  statusEl.textContent = "Loading...";
  // Keep the existing cards visible (dimmed) instead of blanking the list,
  // so a refresh doesn't flash an empty state every 30 seconds.
  if (currentArbs.length) arbListEl.classList.add("is-refreshing");

  try {
    const params = new URLSearchParams({
      books: Array.from(selectedBookIds).join(","),
      sport: Array.from(selectedSportIds).join(","),
      league: selectedSportIds.size === 1 ? currentLeague : "all",
    });
    const res = await fetch(`/api/arbs?${params}`);
    const data = await res.json();
    currentArbs = data.arbs || [];

    if (data.source === "mock") {
      statusEl.textContent = "Showing sample data — add your SharpAPI key to see live arbs.";
    } else if (data.source === "error") {
      statusEl.textContent = `Couldn't reach SharpAPI (${data.error}). Showing sample data instead.`;
    } else if (data.rows_scanned === 0) {
      statusEl.textContent = "SharpAPI returned no odds at all for this sport/league/book combination right now — try a different sport, league, or fewer restrictive book toggles.";
    } else {
      const shown = getFilteredArbs().length;
      statusEl.textContent = `Live data · ${shown} opportunit${shown === 1 ? "y" : "ies"} shown · updated ${new Date().toLocaleTimeString()}`;
    }
    statusEl.textContent += describeBookIssues(data.book_issues);

    renderArbs();
  } catch (err) {
    statusEl.textContent = "Failed to load. Check your connection and try again.";
    console.error(err);
  } finally {
    isLoading = false;
    refreshBtn.disabled = false;
    statusEl.classList.remove("is-loading");
    arbListEl.classList.remove("is-refreshing");
  }
}

function getFilteredArbs() {
  const filter = liveFilterSelect.value;
  let result = currentArbs;
  if (filter === "live") result = result.filter((a) => a.is_live);
  else if (filter === "prematch") result = result.filter((a) => !a.is_live);

  // NBA season brings many more simultaneous arbs than NFL Sundays ever
  // did - a minimum-profit filter keeps the list usable instead of
  // scrolling past dozens of sub-1% opportunities to find the good ones.
  const minProfit = parseFloat(minProfitFilterInput.value);
  if (Number.isFinite(minProfit) && minProfit > 0) {
    result = result.filter((a) => a.profit_percent >= minProfit);
  }

  if (sortBySelect.value === "soonest") {
    // Arbs already arrive sorted by profit_percent descending from the
    // backend (compute_arbs_from_odds/fetch_arbs_paid both sort that way)
    // - only re-sort when the user asked for start-time order instead.
    // Arbs with no known start time sort last rather than first.
    result = [...result].sort((a, b) => {
      const ta = a.event_start_time ? new Date(a.event_start_time).getTime() : Infinity;
      const tb = b.event_start_time ? new Date(b.event_start_time).getTime() : Infinity;
      return ta - tb;
    });
  }

  return result;
}

function formatEventTime(isoString) {
  if (!isoString) return "Start time unknown";
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return "Start time unknown";
  return d.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// Deterministic color per book, from a fixed palette - no real sportsbook
// logos are hosted here (an external asset dependency this app avoids),
// so a stable colored badge is the substitute for "book logo" as a quick
// visual anchor when scanning many rows.
const BOOK_BADGE_COLORS = [
  "#e5484d", "#5b8def", "#3ddc84", "#f5a524", "#a855f7",
  "#ec4899", "#14b8a6", "#f97316", "#6366f1", "#84cc16",
];
function bookBadgeColor(name) {
  let hash = 0;
  for (let i = 0; i < (name || "").length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return BOOK_BADGE_COLORS[hash % BOOK_BADGE_COLORS.length];
}

function renderArbs() {
  let totalStake = parseFloat(totalStakeInput.value);
  if (!Number.isFinite(totalStake) || totalStake < 0) totalStake = 0;

  const arbsToShow = getFilteredArbs();

  if (!arbsToShow.length) {
    arbListEl.innerHTML = `<div class="empty">No arbitrage opportunities match the current filters. Try refreshing or changing the sport/live filter.</div>`;
    return;
  }

  const rowsHtml = arbsToShow
    .map((arb) => {
      const legsHtml = arb.legs
        .map((leg) => {
          const stakeAmount = (totalStake * (leg.stake_percent / 100)).toFixed(2);
          const betBtn = leg.deep_link
            ? `<a class="leg-bet-btn" href="${leg.deep_link}" target="_blank" rel="noopener noreferrer">Bet ↗</a>`
            : "";
          const name = escapeHtml(leg.sportsbook);
          return `
            <div class="leg-chip">
              <span class="leg-book" style="background:${bookBadgeColor(leg.sportsbook)}22;color:${bookBadgeColor(leg.sportsbook)};border-color:${bookBadgeColor(leg.sportsbook)}55;">${name}</span>
              <span class="leg-sel" title="${name}: ${escapeHtml(leg.selection)}">${escapeHtml(leg.selection)}</span>
              <span class="leg-odds">${escapeHtml(leg.odds_american)}</span>
              <span class="leg-stake">$${stakeAmount}</span>
              ${betBtn}
            </div>
          `;
        })
        .join("");

      const profitDollar = (totalStake * (arb.profit_percent / 100)).toFixed(2);
      const liveBadge = arb.is_live ? `<span class="live-badge">LIVE</span>` : "";

      return `
        <div class="arb-row">
          <div class="td td-league">${escapeHtml(arb.league) || "—"}</div>
          <div class="td td-market">${escapeHtml(arb.market) || "—"}</div>
          <div class="td td-game">
            <div class="game-name">${escapeHtml(arb.event_name)}${liveBadge}</div>
            <div class="game-time">${formatEventTime(arb.event_start_time)}</div>
          </div>
          <div class="td td-profit">
            <span class="profit-pill">+${arb.profit_percent.toFixed(2)}%</span>
            <span class="profit-dollar">+$${profitDollar}</span>
          </div>
          <div class="td td-legs">${legsHtml}</div>
        </div>
      `;
    })
    .join("");

  arbListEl.innerHTML = `
    <div class="arb-table">
      <div class="arb-table-header">
        <div class="th th-league">League</div>
        <div class="th th-market">Market</div>
        <div class="th th-game">Game</div>
        <div class="th th-profit">Profit</div>
        <div class="th th-legs">Legs</div>
      </div>
      ${rowsHtml}
    </div>
  `;
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  autoRefreshTimer = setInterval(loadArbs, computeAutoRefreshIntervalMs());
}

// Re-arms the timer at the (possibly new) interval for the current sport/
// book selection - called whenever that selection changes, so a heavier
// scan automatically slows down instead of silently drifting off-budget.
function restartAutoRefreshIfRunning() {
  if (autoRefreshTimer) startAutoRefresh();
}

autoRefreshCheckbox.addEventListener("change", () => {
  if (autoRefreshCheckbox.checked) {
    startAutoRefresh();
  } else {
    stopAutoRefresh();
  }
});

// Pause auto-refresh while the tab is hidden so it doesn't burn API calls
// in the background, and catch up immediately when the user comes back.
document.addEventListener("visibilitychange", () => {
  if (!autoRefreshCheckbox.checked) return;
  if (document.hidden) {
    stopAutoRefresh();
  } else {
    loadArbs();
    startAutoRefresh();
  }
});

leagueSelectEl.addEventListener("change", () => {
  currentLeague = leagueSelectEl.value;
  if (currentLeague) loadArbs();
});

// Shared by every control that only narrows/reorders the already-loaded
// arb list (live/prematch, min profit, sort) rather than needing a new
// server request - re-renders in place and refreshes the shown-count in
// the status line without disturbing an in-progress "Loading..." message.
function refreshFilteredView() {
  updateFiltersSummary();
  renderArbs();
  const shown = getFilteredArbs().length;
  if (statusEl.textContent.startsWith("Live data")) {
    statusEl.textContent = `Live data · ${shown} opportunit${shown === 1 ? "y" : "ies"} shown · updated ${new Date().toLocaleTimeString()}`;
  }
}

liveFilterSelect.addEventListener("change", refreshFilteredView);
minProfitFilterInput.addEventListener("input", refreshFilteredView);
sortBySelect.addEventListener("change", refreshFilteredView);

refreshBtn.addEventListener("click", loadArbs);
totalStakeInput.addEventListener("input", renderArbs);

// Restore the collapsed/expanded state from last visit (defaults to
// expanded, since a first-time visitor needs to see the controls at all).
let startCollapsed = false;
try {
  startCollapsed = localStorage.getItem(FILTERS_COLLAPSED_KEY) === "1";
} catch (err) {
  // private browsing / blocked storage - fine to just default to expanded
}
setFiltersCollapsed(startCollapsed);

let sportsSectionStartCollapsed = false;
try {
  sportsSectionStartCollapsed = localStorage.getItem(SPORTS_SECTION_COLLAPSED_KEY) === "1";
} catch (err) {
  // private browsing / blocked storage - fine to just default to expanded
}
setSectionCollapsed(sportsToggleBtn, sportsPanelEl, SPORTS_SECTION_COLLAPSED_KEY, sportsSectionStartCollapsed);

let booksSectionStartCollapsed = false;
try {
  booksSectionStartCollapsed = localStorage.getItem(BOOKS_SECTION_COLLAPSED_KEY) === "1";
} catch (err) {
  // private browsing / blocked storage - fine to just default to expanded
}
setSectionCollapsed(booksToggleBtn, booksSectionBodyEl, BOOKS_SECTION_COLLAPSED_KEY, booksSectionStartCollapsed);

sportsFavoritesOnlyToggle.checked = sportsFavoritesOnly;
booksFavoritesOnlyToggle.checked = booksFavoritesOnly;

// Initial load: get books and sports in parallel, then leagues if exactly
// one sport ended up preselected, then the first arb scan.
Promise.all([loadBooks(), loadSports()]).then(async () => {
  if (selectedSportIds.size === 1) {
    leagueRowEl.hidden = false;
    await loadLeagues([...selectedSportIds][0]);
  } else {
    leagueRowEl.hidden = true;
  }
  updateScanScopeState();
  updateFiltersSummary();
  loadArbs();
  if (autoRefreshCheckbox.checked) startAutoRefresh();
});
