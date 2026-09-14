const refreshBtn = document.getElementById("refreshBtn");
const totalStakeInput = document.getElementById("totalStake");
const arbListEl = document.getElementById("arbList");
const statusEl = document.getElementById("status");
const booksPanelEl = document.getElementById("booksPanel");
const toggleAllBtn = document.getElementById("toggleAllBtn");
const autoRefreshCheckbox = document.getElementById("autoRefresh");
const sportSelectEl = document.getElementById("sportSelect");
const leagueSelectEl = document.getElementById("leagueSelect");
const sportLeagueNoteEl = document.getElementById("sportLeagueNote");
const scanScopeNoteEl = document.getElementById("scanScopeNote");
const liveFilterSelect = document.getElementById("liveFilter");

const AUTO_REFRESH_INTERVAL_MS = 30000;
const PREFERRED_DEFAULT_SPORT = "football";
const PREFERRED_DEFAULT_LEAGUE = "nfl";

let currentArbs = [];
let allBooks = [];          // [{id, display_name}, ...]
let selectedBookIds = new Set();
let isLoading = false;
let autoRefreshTimer = null;
let currentSport = "";
let currentLeague = "";

function showSportLeagueNote(text) {
  if (!text) {
    sportLeagueNoteEl.hidden = true;
    return;
  }
  sportLeagueNoteEl.textContent = text;
  sportLeagueNoteEl.hidden = false;
}

async function loadSports() {
  try {
    const res = await fetch("/api/sports");
    const data = await res.json();
    const sports = data.sports || [];
    const options = [{ id: "all", name: "All Sports" }, ...sports];
    sportSelectEl.innerHTML = options.map((s) => `<option value="${s.id}">${s.name}</option>`).join("");

    if (data.source === "error") {
      showSportLeagueNote(`Couldn't reach SharpAPI's sports list (${data.error}) — showing a short fallback list instead.`);
    }

    const preferred = sports.find((s) => s.id === PREFERRED_DEFAULT_SPORT);
    currentSport = preferred ? preferred.id : (sports[0] ? sports[0].id : "all");
    sportSelectEl.value = currentSport;
  } catch (err) {
    console.error("Failed to load sports:", err);
    sportSelectEl.innerHTML = `<option value="all">All Sports</option>`;
    currentSport = "all";
    showSportLeagueNote('Couldn\'t load the sports list — defaulting to "All Sports".');
  }
}

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

    const preferred = sport === PREFERRED_DEFAULT_SPORT ? leagues.find((l) => l.id === PREFERRED_DEFAULT_LEAGUE) : null;
    currentLeague = preferred ? preferred.id : leagues[0].id;
    leagueSelectEl.value = currentLeague;
  } catch (err) {
    console.error("Failed to load leagues:", err);
    leagueSelectEl.innerHTML = `<option value="all">All Leagues</option>`;
    currentLeague = "all";
    showSportLeagueNote('Couldn\'t load the specific leagues list — you can still pick "All Leagues" to scan broadly.');
  }
}

function updateScanScopeState() {
  const heavy = currentSport === "all" || currentLeague === "all";
  if (heavy) {
    const scope = currentSport === "all"
      ? "every sport"
      : `all leagues in ${sportSelectEl.options[sportSelectEl.selectedIndex] ? sportSelectEl.options[sportSelectEl.selectedIndex].text : currentSport}`;
    scanScopeNoteEl.textContent = `Scanning ${scope} means a lot more API requests per refresh — auto-refresh has been turned off so this doesn't run automatically every 30 seconds. Use the Refresh button when you want to re-scan.`;
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
    allBooks = data.books || [];
    // Default: all books selected
    selectedBookIds = new Set(allBooks.map((b) => b.id));
    renderBooksPanel();
  } catch (err) {
    console.error("Failed to load books:", err);
  }
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
      return `
        <label class="book-toggle">
          <input type="checkbox" data-book-id="${b.id}" ${checked} />
          ${b.display_name}
        </label>
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
      loadArbs(); // re-scan immediately with the new book selection
    });
  });

  updateToggleAllLabel();
}

function updateToggleAllLabel() {
  const allSelected = allBooks.length > 0 && selectedBookIds.size === allBooks.length;
  toggleAllBtn.textContent = allSelected ? "Deselect All" : "Select All";
}

toggleAllBtn.addEventListener("click", () => {
  const allSelected = allBooks.length > 0 && selectedBookIds.size === allBooks.length;
  selectedBookIds = allSelected ? new Set() : new Set(allBooks.map((b) => b.id));
  renderBooksPanel();
  loadArbs();
});

async function loadArbs() {
  if (isLoading) return; // don't stack overlapping requests (manual click + auto-refresh + toggle spam)

  if (!currentSport || !currentLeague) return; // sports/leagues haven't finished loading yet

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
      sport: currentSport,
      league: currentLeague,
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
          return `
            <div class="leg-col">
              <div class="leg-book">${leg.sportsbook}</div>
              <div class="leg-selection">${leg.selection}</div>
              <div class="leg-odds">${leg.odds_american}</div>
              <div class="leg-stake">Bet $${stakeAmount}</div>
              <div class="leg-stake-pct">${leg.stake_percent.toFixed(1)}% of stake</div>
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

sportSelectEl.addEventListener("change", async () => {
  currentSport = sportSelectEl.value;
  if (currentSport === "all") {
    // No single sport means no single league either - scanning "all sports"
    // already implies every league within each of them.
    leagueSelectEl.innerHTML = `<option value="all">All Leagues</option>`;
    leagueSelectEl.value = "all";
    leagueSelectEl.disabled = true;
    currentLeague = "all";
    showSportLeagueNote(null);
  } else {
    leagueSelectEl.disabled = false;
    await loadLeagues(currentSport);
  }
  updateScanScopeState();
  loadArbs();
});

leagueSelectEl.addEventListener("change", () => {
  currentLeague = leagueSelectEl.value;
  updateScanScopeState();
  if (currentLeague) loadArbs();
});

liveFilterSelect.addEventListener("change", () => {
  renderArbs();
  const shown = getFilteredArbs().length;
  if (statusEl.textContent.startsWith("Live data")) {
    statusEl.textContent = `Live data · ${shown} opportunit${shown === 1 ? "y" : "ies"} shown · updated ${new Date().toLocaleTimeString()}`;
  }
});

refreshBtn.addEventListener("click", loadArbs);
totalStakeInput.addEventListener("input", renderArbs);

// Initial load: get books and sports in parallel, then leagues for the
// default sport, then the first arb scan.
Promise.all([loadBooks(), loadSports()]).then(async () => {
  if (currentSport && currentSport !== "all") await loadLeagues(currentSport);
  updateScanScopeState();
  loadArbs();
  if (autoRefreshCheckbox.checked) startAutoRefresh();
});
