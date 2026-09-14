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

const FILTERS_COLLAPSED_KEY = "arbScreenerFiltersCollapsed";
const SPORTS_SECTION_COLLAPSED_KEY = "arbScreenerSportsSectionCollapsed";
const BOOKS_SECTION_COLLAPSED_KEY = "arbScreenerBooksSectionCollapsed";
const CUSTOM_BOOKS_KEY = "arbScreenerCustomBooks";
const SELECTED_BOOK_IDS_KEY = "arbScreenerSelectedBookIds";

// Confirmed rate limit for the Hobby plan (per SharpAPI's own docs):
// 120 requests/minute. A normal scan (one book toggled on = one request)
// at this interval stays well under budget even with all 5 plan books
// selected (5 req / 15s = 20 req/min, ~17% of the limit).
const AUTO_REFRESH_INTERVAL_MS = 15000;
const PREFERRED_DEFAULT_LEAGUE = "nfl";

let currentArbs = [];
let allBooks = [];          // [{id, display_name}, ...]
let selectedBookIds = new Set();
let allSports = [];         // [{id, name}, ...]
let selectedSportIds = new Set();
let currentLeague = "all";  // only meaningful when exactly one sport is selected
let isLoading = false;
let autoRefreshTimer = null;

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

function renderSportsPanel() {
  if (!allSports.length) {
    sportsPanelEl.innerHTML = "";
    toggleAllSportsBtn.style.display = "none";
    return;
  }
  toggleAllSportsBtn.style.display = "inline-block";

  sportsPanelEl.innerHTML = allSports
    .map((s) => {
      const checked = selectedSportIds.has(s.id) ? "checked" : "";
      return `
        <label class="book-toggle">
          <input type="checkbox" data-sport-id="${s.id}" ${checked} />
          ${s.name}
        </label>
      `;
    })
    .join("");

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

  updateToggleAllSportsLabel();
  updateSportsSectionSummary();
}

function updateToggleAllSportsLabel() {
  const allSelected = allSports.length > 0 && selectedSportIds.size === allSports.length;
  toggleAllSportsBtn.textContent = allSelected ? "Deselect All" : "Select All";
}

toggleAllSportsBtn.addEventListener("click", async () => {
  const allSelected = allSports.length > 0 && selectedSportIds.size === allSports.length;
  selectedSportIds = allSelected ? new Set() : new Set(allSports.map((s) => s.id));
  renderSportsPanel();
  updateFiltersSummary();
  updateSportsSectionSummary();
  await onSportSelectionChanged();
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
  // Only looping across MULTIPLE sports multiplies request count (one
  // request per sport per book) - a single sport with "All Leagues" is
  // still just one request per book, exactly as cheap as one specific
  // league, so it does NOT need to disable auto-refresh.
  const heavy = selectedSportIds.size > 1;
  if (heavy) {
    scanScopeNoteEl.textContent = `Scanning ${selectedSportIds.size} sports means one API request per sport per book selected — with your 120 requests/minute limit, a single scan like this can use a meaningful chunk of that budget at once. Auto-refresh has been turned off so it doesn't repeat automatically; use the Refresh button when you want to re-scan.`;
    scanScopeNoteEl.hidden = false;
    autoRefreshCheckbox.checked = false;
    autoRefreshCheckbox.disabled = true;
    stopAutoRefresh();
  } else {
    scanScopeNoteEl.hidden = true;
    autoRefreshCheckbox.disabled = false;
  }
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

function renderBooksPanel() {
  if (!allBooks.length) {
    booksPanelEl.innerHTML = "";
    toggleAllBtn.style.display = "none";
    return;
  }
  toggleAllBtn.style.display = "inline-block";

  booksPanelEl.innerHTML = allBooks
    .map((b) => {
      const checked = selectedBookIds.has(b.id) ? "checked" : "";
      const name = escapeHtml(b.display_name);
      // Lets you see at a glance why a toggled-on book might return
      // nothing - it needs a higher SharpAPI plan tier than you're on.
      const tierLabel = b.tier ? TIER_LABELS[b.tier] || b.tier : null;
      const labelTitle = tierLabel ? ` title="Requires ${escapeHtml(tierLabel)} tier or higher on SharpAPI"` : "";
      // The remove (x) button is a sibling of the label, not nested inside
      // it - nesting a button inside a <label> wrapping a checkbox causes
      // browsers to double-toggle the checkbox when the button is clicked.
      const removeBtn = b.custom
        ? `<button type="button" class="book-remove-btn" data-remove-book-id="${escapeHtml(b.id)}" title="Remove ${name}" aria-label="Remove ${name}">&times;</button>`
        : "";
      return `
        <label class="book-toggle"${labelTitle}>
          <input type="checkbox" data-book-id="${escapeHtml(b.id)}" ${checked} />
          ${name}${tierLabel ? `<span class="book-tier-badge">${escapeHtml(tierLabel)}</span>` : ""}
        </label>${removeBtn}
      `;
    })
    .join("");

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
      loadArbs(); // re-scan immediately with the new book selection
    });
  });

  booksPanelEl.querySelectorAll("button[data-remove-book-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-remove-book-id");
      allBooks = allBooks.filter((b) => b.id !== id);
      selectedBookIds.delete(id);
      saveCustomBooksToStorage();
      saveSelectedBookIdsToStorage();
      renderBooksPanel();
      updateFiltersSummary();
      updateBooksSectionSummary();
      loadArbs();
    });
  });

  updateToggleAllLabel();
  updateBooksSectionSummary();
}

function updateToggleAllLabel() {
  const allSelected = allBooks.length > 0 && selectedBookIds.size === allBooks.length;
  toggleAllBtn.textContent = allSelected ? "Deselect All" : "Select All";
}

toggleAllBtn.addEventListener("click", () => {
  const allSelected = allBooks.length > 0 && selectedBookIds.size === allBooks.length;
  selectedBookIds = allSelected ? new Set() : new Set(allBooks.map((b) => b.id));
  renderBooksPanel();
  updateFiltersSummary();
  saveSelectedBookIdsToStorage();
  loadArbs();
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
  if (filter === "live") return currentArbs.filter((a) => a.is_live);
  if (filter === "prematch") return currentArbs.filter((a) => !a.is_live);
  return currentArbs;
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

function renderArbs() {
  let totalStake = parseFloat(totalStakeInput.value);
  if (!Number.isFinite(totalStake) || totalStake < 0) totalStake = 0;

  const arbsToShow = getFilteredArbs();

  if (!arbsToShow.length) {
    arbListEl.innerHTML = `<div class="empty">No arbitrage opportunities match the current filters. Try refreshing or changing the sport/live filter.</div>`;
    return;
  }

  arbListEl.innerHTML = arbsToShow
    .map((arb) => {
      const legsHtml = arb.legs
        .map((leg) => {
          const stakeAmount = (totalStake * (leg.stake_percent / 100)).toFixed(2);
          const placeBetBtn = leg.deep_link
            ? `<a class="place-bet-btn" href="${leg.deep_link}" target="_blank" rel="noopener noreferrer">Place Bet →</a>`
            : "";
          return `
            <div class="leg-col">
              <div class="leg-book">${leg.sportsbook}</div>
              <div class="leg-selection">${leg.selection}</div>
              <div class="leg-odds">${leg.odds_american}</div>
              <div class="leg-stake">Bet $${stakeAmount}</div>
              <div class="leg-stake-pct">${leg.stake_percent.toFixed(1)}% of stake</div>
              ${placeBetBtn}
            </div>
          `;
        })
        .join(`<div class="leg-divider">vs</div>`);

      const profitDollar = (totalStake * (arb.profit_percent / 100)).toFixed(2);
      const liveBadge = arb.is_live ? `<span class="live-badge">LIVE</span>` : "";

      return `
        <div class="arb-card">
          <div class="arb-card-header">
            <div>
              <div class="arb-event">${arb.event_name}${liveBadge}</div>
              <div class="arb-league">${arb.league || ""}</div>
              <div class="arb-event-time">${formatEventTime(arb.event_start_time)}</div>
            </div>
            <div>
              <div class="arb-profit">+${arb.profit_percent.toFixed(2)}%</div>
              <div class="arb-profit-dollar">+$${profitDollar} on $${totalStake.toFixed(2)}</div>
            </div>
          </div>
          <div class="legs-row">${legsHtml}</div>
        </div>
      `;
    })
    .join("");
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  autoRefreshTimer = setInterval(loadArbs, AUTO_REFRESH_INTERVAL_MS);
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

liveFilterSelect.addEventListener("change", () => {
  updateFiltersSummary();
  renderArbs();
  const shown = getFilteredArbs().length;
  if (statusEl.textContent.startsWith("Live data")) {
    statusEl.textContent = `Live data · ${shown} opportunit${shown === 1 ? "y" : "ies"} shown · updated ${new Date().toLocaleTimeString()}`;
  }
});

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
