const refreshBtn = document.getElementById("refreshBtn");
const totalStakeInput = document.getElementById("totalStake");
const arbListEl = document.getElementById("arbList");
const statusEl = document.getElementById("status");
const booksPanelEl = document.getElementById("booksPanel");
const toggleAllBtn = document.getElementById("toggleAllBtn");
const autoRefreshCheckbox = document.getElementById("autoRefresh");
const sportPresetSelect = document.getElementById("sportPreset");
const customSportFieldsEl = document.getElementById("customSportFields");
const customSportInput = document.getElementById("customSport");
const customLeagueInput = document.getElementById("customLeague");
const applyCustomSportBtn = document.getElementById("applyCustomSportBtn");

const AUTO_REFRESH_INTERVAL_MS = 30000;

let currentArbs = [];
let allBooks = [];          // [{id, display_name}, ...]
let selectedBookIds = new Set();
let isLoading = false;
let autoRefreshTimer = null;
let currentSport = "football";
let currentLeague = "nfl";

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
    } else {
      statusEl.textContent = `Live data · ${currentArbs.length} opportunit${currentArbs.length === 1 ? "y" : "ies"} found · updated ${new Date().toLocaleTimeString()}`;
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

function renderArbs() {
  let totalStake = parseFloat(totalStakeInput.value);
  if (!Number.isFinite(totalStake) || totalStake < 0) totalStake = 0;

  if (!currentArbs.length) {
    arbListEl.innerHTML = `<div class="empty">No arbitrage opportunities right now. Try refreshing in a bit.</div>`;
    return;
  }

  arbListEl.innerHTML = currentArbs
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

      return `
        <div class="arb-card">
          <div class="arb-card-header">
            <div>
              <div class="arb-event">${arb.event_name}</div>
              <div class="arb-league">${arb.league || ""}</div>
            </div>
            <div class="arb-profit">+${arb.profit_percent.toFixed(2)}%</div>
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

function setSportLeague(sport, league) {
  currentSport = sport;
  currentLeague = league;
  loadArbs();
}

sportPresetSelect.addEventListener("change", () => {
  const value = sportPresetSelect.value;
  if (value === "custom") {
    customSportInput.value = currentSport;
    customLeagueInput.value = currentLeague;
    customSportFieldsEl.hidden = false;
    return;
  }
  customSportFieldsEl.hidden = true;
  const [sport, league] = value.split(":");
  setSportLeague(sport, league);
});

applyCustomSportBtn.addEventListener("click", () => {
  const sport = customSportInput.value.trim().toLowerCase();
  const league = customLeagueInput.value.trim().toLowerCase();
  if (!sport || !league) return;
  setSportLeague(sport, league);
});

refreshBtn.addEventListener("click", loadArbs);
totalStakeInput.addEventListener("input", renderArbs);

// Initial load: get available books first, then load arbs
loadBooks().then(() => {
  loadArbs();
  if (autoRefreshCheckbox.checked) startAutoRefresh();
});
