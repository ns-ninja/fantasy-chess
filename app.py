import streamlit as st
import pandas as pd
import random
import math
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Chess Swiss Tournament Simulator",
    page_icon="♟️",
    layout="wide",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-title {
        font-size: 2.4rem;
        font-weight: 800;
        color: #1a1a2e;
        letter-spacing: -1px;
    }
    .section-header {
        font-size: 1.1rem;
        font-weight: 700;
        color: #2c3e50;
        border-bottom: 2px solid #3498db;
        padding-bottom: 4px;
        margin-bottom: 12px;
    }
    .round-badge {
        background: #2980b9;
        color: white;
        padding: 2px 10px;
        border-radius: 12px;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .result-win  { color: #27ae60; font-weight: bold; }
    .result-loss { color: #e74c3c; font-weight: bold; }
    .result-draw { color: #f39c12; font-weight: bold; }
    .stDataFrame tbody tr:first-child { background: #fff9e6 !important; }
    .gold { color: #f1c40f; font-size: 1.2rem; }
    .silver { color: #bdc3c7; font-size: 1.2rem; }
    .bronze { color: #cd7f32; font-size: 1.2rem; }
    .info-box {
        background: #eaf4fb;
        border-left: 4px solid #2980b9;
        padding: 10px 16px;
        border-radius: 0 6px 6px 0;
        margin: 8px 0;
    }
</style>
""", unsafe_allow_html=True)


# ── Session State Init ────────────────────────────────────────────────────────
def init_state():
    defaults = {
        "players": [],
        "rounds": [],            # list of completed round pairing dicts
        "pending_pairings": [],  # pairings generated, not yet simulated
        "tournament_started": False,
        "tournament_done": False,
        "num_rounds": 5,
        "current_round": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()


# ── Tournament Persistence ────────────────────────────────────────────────────

def get_tournaments_file():
    """Get the path to the tournaments history file.
    Uses a local data directory that persists on Streamlit Cloud."""
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir / "tournaments.json"


def load_tournaments():
    """Load all saved tournaments from file."""
    tfile = get_tournaments_file()
    if tfile.exists():
        try:
            with open(tfile, 'r') as f:
                return json.load(f)
        except Exception as e:
            st.warning(f"Could not load tournament history: {e}")
            return []
    return []


def save_tournament(tournament_data):
    """Save a completed tournament to file."""
    tfile = get_tournaments_file()
    tournaments = load_tournaments()
    tournaments.append(tournament_data)
    try:
        with open(tfile, 'w') as f:
            json.dump(tournaments, f, indent=2, default=str)
    except Exception as e:
        st.error(f"Could not save tournament: {e}")


def get_tournament_data():
    """Package current tournament data for saving."""
    standings = get_standings()
    rating_changes = st.session_state.get("rating_changes", {})
    
    # Build game records for each player
    games_data = {}
    for p in st.session_state.players:
        games_data[p["name"]] = {
            "results": p["results"],
            "colors": p["colors"]
        }
    
    return {
        "id": datetime.now().isoformat(),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "num_players": len(st.session_state.players),
        "num_rounds": st.session_state.num_rounds,
        "upset_factor": st.session_state.get("upset", 0.06),
        "standings": [
            {
                "rank": idx + 1,
                "name": row["Name"],
                "rating": row["Rating"],
                "score": row["Score"],
                "buchholz": row["Buchholz"],
            }
            for idx, row in enumerate(standings)
        ],
        "rating_changes": rating_changes,
        "players_initial": [
            {
                "name": p["name"],
                "rating": st.session_state.original_ratings.get(p["name"], p["rating"])
            }
            for p in st.session_state.players
        ],
        "games_data": games_data
    }


def aggregate_player_stats(player_name):
    """Aggregate opponent stats for a player across all tournaments.
    Returns a dict: opponent_name -> {games, wins, draws, losses}"""
    tournaments = load_tournaments()
    opponent_stats = defaultdict(lambda: {"games": 0, "wins": 0, "draws": 0, "losses": 0})
    
    for tourn in tournaments:
        games = tourn.get("games_data", {})
        if player_name not in games:
            continue
        
        player_games = games[player_name]
        results = player_games.get("results", [])
        
        for opponent_name, score in results:
            if opponent_name == "BYE":
                continue
            
            stats = opponent_stats[opponent_name]
            stats["games"] += 1
            
            if score == 1.0:
                stats["wins"] += 1
            elif score == 0.5:
                stats["draws"] += 1
            elif score == 0.0:
                stats["losses"] += 1
    
    return opponent_stats


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_player(name):
    for p in st.session_state.players:
        if p["name"] == name:
            return p
    return None


def fresh_player(name, rating):
    return {
        "name": name,
        "rating": int(rating),
        "max_rating": int(rating),  # track highest rating achieved for floor calculation
        "score": 0.0,
        "colors": [],          # list of 'W' or 'B' each round
        "opponents": set(),    # names of past opponents
        "has_bye": False,
        "results": [],         # list of (opponent_name, score_earned)
        "buchholz": 0.0,
        "cumulative": [],      # running score after each round for tie-break
    }


def reset_players():
    for p in st.session_state.players:
        p.update({
            "score": 0.0,
            "colors": [],
            "opponents": set(),
            "has_bye": False,
            "results": [],
            "buchholz": 0.0,
            "cumulative": [],
        })


# ── Simulation ────────────────────────────────────────────────────────────────

def expected_score(ra, rb):
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def simulate_result(ra, rb):
    """
    Returns (white_score, black_score).
    Uses Elo-based probabilities + draw curve + small upset factor.
    """
    diff = abs(ra - rb)

    # Draw probability: highest when equal, drops off with rating gap
    draw_prob = 0.32 * math.exp(-diff / 280.0)
    draw_prob = max(0.04, min(0.50, draw_prob))

    if random.random() < draw_prob:
        return 0.5, 0.5

    # Decisive game: win probability with a slight upset factor
    raw_win = expected_score(ra, rb)
    upset = st.session_state.get("upset", 0.06)
    win_prob = raw_win * (1.0 - upset) + 0.5 * upset
    win_prob = max(0.05, min(0.95, win_prob))

    if random.random() < win_prob:
        return 1.0, 0.0
    else:
        return 0.0, 1.0


# ── Color Assignment ──────────────────────────────────────────────────────────

def color_imbalance(p):
    """Positive → more whites than blacks (wants black). Negative → wants white."""
    return p["colors"].count("W") - p["colors"].count("B")


def assign_colors(p1, p2):
    """Returns (white_player, black_player) to balance color histories."""
    d1 = color_imbalance(p1)
    d2 = color_imbalance(p2)
    if d1 > d2:          # p1 has more whites → give p1 black
        return p2, p1
    elif d2 > d1:
        return p1, p2
    elif p1["rating"] < p2["rating"]:   # equal imbalance: higher rated gets desired color
        return p1, p2
    else:
        return p2, p1


# ── USCF Swiss Pairing ────────────────────────────────────────────────────────

def make_pairings(players):
    """
    USCF-style Swiss system:
      1. Sort by score desc, rating desc.
      2. Group into score brackets.
      3. Within each bracket split top/bottom half and cross-pair.
      4. Float unpaired players down to next bracket.
      5. Avoid rematches; force rematch only as absolute last resort.
      6. Assign bye to lowest-rated player without one (odd player count).
    """
    sorted_players = sorted(players, key=lambda p: (-p["score"], -p["rating"]))

    bye_player = None
    pool = list(sorted_players)

    if len(pool) % 2 == 1:
        for candidate in reversed(pool):
            if not candidate["has_bye"]:
                bye_player = candidate
                pool.remove(candidate)
                break
        if bye_player is None:
            bye_player = pool.pop()

    # Group by score (rounded to nearest 0.5)
    score_groups = defaultdict(list)
    for p in pool:
        key = round(p["score"] * 2) / 2
        score_groups[key].append(p)

    scores_desc = sorted(score_groups.keys(), reverse=True)

    pairings = []
    leftover = []

    def _pair_list_no_rematch(lst):
        # Return list of (name1, name2) pairs or None if impossible
        if len(lst) % 2 == 1:
            return None

        # Work with player dicts
        names = [p["name"] for p in lst]
        name_to_p = {p["name"]: p for p in lst}

        # Recursive backtracking
        def helper(remaining):
            if not remaining:
                return []
            a = remaining[0]
            for i in range(1, len(remaining)):
                b = remaining[i]
                # skip if they already played
                if b in name_to_p[a]["opponents"]:
                    continue
                rest = remaining[1:i] + remaining[i+1:]
                sub = helper(rest)
                if sub is not None:
                    return [(a, b)] + sub
            return None

        return helper(names)

    for score in scores_desc:
        group = leftover + score_groups[score]
        group.sort(key=lambda p: -p["rating"])
        leftover = []

        half = len(group) // 2
        top  = group[:half]
        bot  = group[half:]

        used_top = set()
        used_bot = set()

        for t in top:
            matched = False
            for b in bot:
                if b["name"] in used_bot:
                    continue
                if b["name"] not in t["opponents"]:
                    w, bl = assign_colors(t, b)
                    pairings.append({"white": w["name"], "black": bl["name"], "result": None})
                    used_top.add(t["name"])
                    used_bot.add(b["name"])
                    matched = True
                    break
            if not matched:
                leftover.append(t)

        for b in bot:
            if b["name"] not in used_bot:
                leftover.append(b)

    # Pair remaining leftovers — attempt to avoid rematches via backtracking
    leftover.sort(key=lambda p: (-p["score"], -p["rating"]))
    if len(leftover) >= 2:
        matching = _pair_list_no_rematch(leftover)
        if matching is None:
            # Try matching across the entire pool (excluding bye) as a fallback
            try_pool = [p for p in sorted_players if p not in ( [bye_player] if bye_player else [] )]
            if len(try_pool) % 2 == 0:
                matching = _pair_list_no_rematch(try_pool)

        if matching is not None:
            # consume matched pairs from leftover where possible, otherwise from pool
            used = set()
            for a_name, b_name in matching:
                a = get_player(a_name)
                b = get_player(b_name)
                if a is None or b is None:
                    continue
                pairings.append({"white": assign_colors(a, b)[0]["name"], "black": assign_colors(a, b)[1]["name"], "result": None})
            leftover = []
        else:
            # As last resort (should be rare), fall back to greedy pairing allowing rematches
            while len(leftover) >= 2:
                p1 = leftover.pop(0)
                match_idx = None
                for i, p2 in enumerate(leftover):
                    if p2["name"] not in p1["opponents"]:
                        match_idx = i
                        break
                if match_idx is None:
                    match_idx = 0
                p2 = leftover.pop(match_idx)
                w, bl = assign_colors(p1, p2)
                pairings.append({"white": w["name"], "black": bl["name"], "result": None})

    if leftover:
        if bye_player is None:
            bye_player = leftover[0]

    if bye_player:
        pairings.append({"white": bye_player["name"], "black": "BYE", "result": None})

    # Order pairings so players with higher standings appear on higher boards
    name_to_index = {p["name"]: idx for idx, p in enumerate(sorted_players)}

    def _pair_key(pair):
        w, b = pair["white"], pair["black"]
        if b == "BYE":
            return 9999  # send BYE to bottom
        wi = name_to_index.get(w, 9999)
        bi = name_to_index.get(b, 9999)
        return min(wi, bi)

    pairings.sort(key=_pair_key)

    return pairings


# ── Apply / Simulate Rounds ───────────────────────────────────────────────────

def simulate_round(pairings):
    results = []
    for p in pairings:
        wn, bn = p["white"], p["black"]
        if bn == "BYE":
            results.append({**p, "result": "1-BYE", "white_score": 1.0, "black_score": 0.0})
            continue
        white_p = get_player(wn)
        black_p = get_player(bn)
        ws, bs = simulate_result(white_p["rating"], black_p["rating"])
        if ws == 1:   res = "1-0"
        elif bs == 1: res = "0-1"
        else:         res = "½-½"
        results.append({**p, "result": res, "white_score": ws, "black_score": bs})
    return results


def apply_results(pairings):
    for p in pairings:
        wn, bn = p["white"], p["black"]
        white = get_player(wn)
        ws = p.get("white_score", 0.0)
        bs = p.get("black_score", 0.0)

        white["score"] += ws
        white["colors"].append("W")
        white["results"].append((bn, ws))
        white["cumulative"].append(white["score"])

        if bn != "BYE":
            black = get_player(bn)
            black["score"] += bs
            black["colors"].append("B")
            black["results"].append((wn, bs))
            black["cumulative"].append(black["score"])
            white["opponents"].add(bn)
            black["opponents"].add(wn)
        else:
            white["has_bye"] = True


# ── Tiebreaks & Standings ─────────────────────────────────────────────────────

def compute_buchholz():
    for p in st.session_state.players:
        total = 0.0
        for opp_name, _ in p["results"]:
            if opp_name == "BYE":
                total += p["score"]
            else:
                opp = get_player(opp_name)
                if opp:
                    total += opp["score"]
        p["buchholz"] = total


def _k_factor(rating):
    # Simple USCF-like tiers: <2100 => 40, 2100-2399 => 20, >=2400 => 10
    try:
        r = int(rating)
    except Exception:
        r = 1500
    if r < 2100:
        return 40
    if r < 2400:
        return 20
    return 10


def compute_and_apply_rating_changes():
    # Requires st.session_state.original_ratings snapshot
    orig = st.session_state.get("original_ratings", {})
    if not orig:
        return {}

    changes = {}
    # Build name->player mapping for lookups
    name_map = {p["name"]: p for p in st.session_state.players}

    for p in st.session_state.players:
        name = p["name"]
        orig_r = orig.get(name, p.get("rating", 1500))
        actual = 0.0
        expected = 0.0
        for opp_name, score in p["results"]:
            if opp_name == "BYE":
                actual += 1.0
                expected += 1.0
                continue
            opp_orig_r = orig.get(opp_name, name_map.get(opp_name, {}).get("rating", 1500))
            actual += float(score)
            expected += expected_score(orig_r, opp_orig_r)

        k = _k_factor(orig_r)
        delta = k * (actual - expected)
        delta_rounded = int(round(delta))
        # Apply change to player rating
        new_rating = int(round(orig_r + delta_rounded))
        
        # Enforce rating floor: cannot drop below (highest_bracket - 200)
        # e.g., if max_rating was 1966, bracket is 1900, floor is 1700
        max_rating = p.get("max_rating", orig_r)
        highest_bracket = (int(max_rating) // 100) * 100
        rating_floor = highest_bracket - 200
        
        new_rating = max(new_rating, rating_floor)
        
        p["rating"] = new_rating
        p["max_rating"] = max(max_rating, new_rating)  # update max_rating if new rating is higher
        p["rating_change"] = delta_rounded
        changes[name] = {"old": orig_r, "delta": delta_rounded, "new": p["rating"], "floor": rating_floor}

    st.session_state.ratings_applied = True
    return changes


def get_standings():
    compute_buchholz()
    rows = []
    for p in st.session_state.players:
        # Cumulative score tiebreak (sum of running scores)
        cum_tb = sum(p.get("cumulative", []))
        rows.append({
            "Name": p["name"],
            "Rating": p["rating"],
            "Score": p["score"],
            "Buchholz": round(p["buchholz"], 1),
            "Cumulative": round(cum_tb, 1),
            "_player": p,
        })
    rows.sort(key=lambda r: (-r["Score"], -r["Buchholz"], -r["Cumulative"], -r["Rating"]))
    return rows


def score_str(s):
    if s == 1.0:  return "1"
    if s == 0.5:  return "½"
    if s == 0.0:  return "0"
    return str(s)


def result_icon(s):
    if s == 1.0:  return "✅"
    if s == 0.5:  return "🟡"
    if s == 0.0:  return "❌"
    return "—"


def rank_medal(i):
    return ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}."


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ♟️ Swiss Tournament")
    st.markdown("---")
    n = len(st.session_state.players)
    r = st.session_state.current_round
    total = st.session_state.num_rounds
    st.markdown(f"**Players:** {n}")
    st.markdown(f"**Rounds:** {r} / {total}")
    if st.session_state.tournament_started and not st.session_state.tournament_done:
        st.progress(r / total, text=f"Round {r}/{total}")
    elif st.session_state.tournament_done:
        st.success("Tournament Complete!")
    st.markdown("---")
    st.markdown("**How results work:**")
    st.markdown("""
- Elo-based win probability  
- Draw rate scales with rating gap  
- Small upset factor included  
- USCF Swiss pairing system  
- Buchholz tiebreak  
""")

    if st.session_state.tournament_started:
        st.markdown("---")
        if st.button("🔄 Reset Tournament", use_container_width=True):
            st.session_state.tournament_started = False
            st.session_state.tournament_done = False
            st.session_state.rounds = []
            st.session_state.pending_pairings = []
            st.session_state.current_round = 0
            reset_players()
            st.rerun()

    if st.button("🗑️ Clear Everything", use_container_width=True):
        for key in ["players","rounds","pending_pairings","tournament_started",
                    "tournament_done","current_round"]:
            if key in st.session_state:
                del st.session_state[key]
        init_state()
        st.rerun()


# ── Main Title ────────────────────────────────────────────────────────────────
st.markdown('<div class="main-title">♟️ Chess Swiss Tournament Simulator</div>', unsafe_allow_html=True)
st.caption("USCF Swiss pairing system · Elo-based simulation · Buchholz tiebreaks")
st.markdown("---")

tab1, tab2, tab3, tab4 = st.tabs(["👥  Players & Setup", "🏟️  Tournament", "📊  Standings", "📜  History"])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 – PLAYERS & SETUP
# ════════════════════════════════════════════════════════════════════════════════
with tab1:

    if st.session_state.tournament_started:
        st.info("Tournament is running. Reset it from the sidebar to modify players.")

    col_left, col_right = st.columns([1, 1], gap="large")

    # ── Individual Add ────────────────────────────────────────────────────────
    with col_left:
        st.markdown('<div class="section-header">Add Individual Player</div>', unsafe_allow_html=True)
        with st.form("add_player_form", clear_on_submit=True):
            pname   = st.text_input("Player Name", placeholder="e.g. Magnus Carlsen")
            prating = st.number_input("Rating", min_value=100, max_value=3300, value=1500, step=10)
            submitted = st.form_submit_button("➕ Add Player", type="primary", use_container_width=True)

        if submitted:
            if not pname.strip():
                st.error("Please enter a name.")
            elif st.session_state.tournament_started:
                st.error("Cannot add players mid-tournament.")
            elif any(p["name"].lower() == pname.strip().lower() for p in st.session_state.players):
                st.warning(f'"{pname.strip()}" is already in the list.')
            else:
                st.session_state.players.append(fresh_player(pname.strip(), prating))
                st.success(f"Added **{pname.strip()}** ({prating})")
                st.rerun()

    # ── Bulk Import ───────────────────────────────────────────────────────────
    with col_right:
        st.markdown('<div class="section-header">Bulk Import</div>', unsafe_allow_html=True)
        st.markdown("""
<div class="info-box">
Paste one player per line. Supports common tournament export formats:<br>
<code>John Smith 1850</code><br>
<code>1. Jane Doe 1720</code><br>
<code>Jane Doe, 1720</code>
</div>
""", unsafe_allow_html=True)
        bulk_text = st.text_area("Paste player list", height=160, placeholder="John Smith 1850\nJane Doe 1720\n...")

        if st.button("📥 Import Players", use_container_width=True):
            if st.session_state.tournament_started:
                st.error("Cannot import mid-tournament.")
            else:
                added, skipped = [], []
                for raw_line in bulk_text.strip().splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    # Strip leading rank number like "1." or "1)"
                    import re
                    line = re.sub(r"^\d+[\.\)]\s*", "", line)
                    # Replace commas/tabs with space
                    line = re.sub(r"[,\t]+", " ", line)
                    parts = line.rsplit(None, 1)
                    if len(parts) == 2:
                        try:
                            pn = parts[0].strip()
                            pr = int(parts[1].strip())
                            if any(p["name"].lower() == pn.lower() for p in st.session_state.players):
                                skipped.append(f"Duplicate: {pn}")
                                continue
                            st.session_state.players.append(fresh_player(pn, pr))
                            added.append(pn)
                        except ValueError:
                            skipped.append(f"Bad line: {raw_line[:40]}")
                    else:
                        skipped.append(f"Cannot parse: {raw_line[:40]}")

                if added:
                    st.success(f"Imported {len(added)} player(s): {', '.join(added[:5])}{'…' if len(added)>5 else ''}")
                    st.rerun()
                for msg in skipped:
                    st.warning(msg)

    st.markdown("---")

    # ── Player List ───────────────────────────────────────────────────────────
    st.markdown(f'<div class="section-header">Registered Players ({len(st.session_state.players)})</div>',
                unsafe_allow_html=True)

    if not st.session_state.players:
        st.info("No players yet. Add some above!")
    else:
        player_display = pd.DataFrame([
            {"#": i+1, "Name": p["name"], "Rating": p["rating"]}
            for i, p in enumerate(sorted(st.session_state.players, key=lambda x: -x["rating"]))
        ])
        st.dataframe(player_display, hide_index=True, use_container_width=True, height=250)

        if not st.session_state.tournament_started:
            rcol1, rcol2 = st.columns([2, 1])
            with rcol1:
                remove_choice = st.selectbox(
                    "Remove a player",
                    options=["— select —"] + [p["name"] for p in st.session_state.players]
                )
            with rcol2:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("Remove", use_container_width=True) and remove_choice != "— select —":
                    st.session_state.players = [p for p in st.session_state.players if p["name"] != remove_choice]
                    st.rerun()

    st.markdown("---")

    # ── Tournament Setup ──────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Tournament Setup</div>', unsafe_allow_html=True)

    if st.session_state.tournament_started:
        st.success(f"Tournament running: {st.session_state.num_rounds} rounds, "
                   f"{len(st.session_state.players)} players.")
    else:
        sc1, sc2, sc3 = st.columns([1, 1, 2])
        with sc1:
            nr = st.number_input("Number of rounds", min_value=1, max_value=15,
                                  value=st.session_state.num_rounds, step=1)
            st.session_state.num_rounds = nr
        with sc3:
            # Upset factor control: influences randomness in simulate_result
            upset_val = st.slider("Upset factor", min_value=0.00, max_value=0.30,
                                  value=st.session_state.get("upset", 0.06), step=0.01,
                                  help="Higher upset → more chances of lower-rated players winning")
            st.session_state.upset = float(upset_val)
        with sc2:
            st.markdown("<br>", unsafe_allow_html=True)
            players_ok = len(st.session_state.players) >= 2
            if st.button("🚀 Start Tournament", type="primary", use_container_width=True,
                          disabled=not players_ok):
                reset_players()
                st.session_state.tournament_started = True
                st.session_state.tournament_done    = False
                st.session_state.rounds             = []
                st.session_state.current_round      = 0
                # Snapshot original ratings for post-tournament rating calculations
                st.session_state.original_ratings = {p["name"]: p["rating"] for p in st.session_state.players}
                st.session_state.ratings_applied = False
                st.session_state.pending_pairings   = make_pairings(st.session_state.players)
                st.rerun()
        with sc3:
            if not players_ok:
                st.warning("Need at least 2 players to start.")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 – TOURNAMENT
# ════════════════════════════════════════════════════════════════════════════════
with tab2:

    if not st.session_state.tournament_started:
        st.info("Set up your players in the **Players & Setup** tab and click **Start Tournament**.")
    else:
        total_rounds   = st.session_state.num_rounds
        current_round  = st.session_state.current_round
        done           = st.session_state.tournament_done

        # ── Progress bar ─────────────────────────────────────────────────────
        prog_cols = st.columns(total_rounds)
        for i, pc in enumerate(prog_cols):
            if i < current_round:
                pc.markdown(f"<center><span style='color:#27ae60;font-size:0.9rem;'>✔ R{i+1}</span></center>",
                            unsafe_allow_html=True)
            elif i == current_round and not done:
                pc.markdown(f"<center><span style='color:#2980b9;font-weight:bold;font-size:0.9rem;'>▶ R{i+1}</span></center>",
                            unsafe_allow_html=True)
            else:
                pc.markdown(f"<center><span style='color:#bbb;font-size:0.9rem;'>R{i+1}</span></center>",
                            unsafe_allow_html=True)

        st.markdown("---")

        # ── Simulate next round ───────────────────────────────────────────────
        if not done:
            st.markdown(f"### Round {current_round + 1} of {total_rounds}")

            pending = st.session_state.pending_pairings
            if pending:
                pair_rows = []
                for i, p in enumerate(pending):
                    wn = p["white"]
                    bn = p["black"]
                    wr = get_player(wn)["rating"] if get_player(wn) else "—"
                    br = get_player(bn)["rating"] if bn != "BYE" and get_player(bn) else "—"
                    if bn != "BYE":
                        exp = expected_score(wr, br) * 100
                        edge = f"{exp:.0f}% / {100-exp:.0f}%"
                    else:
                        edge = "Bye"
                    pair_rows.append({
                        "Brd": i + 1,
                        "White": wn,
                        "W Rating": wr,
                        "  ": "vs",
                        "Black": bn,
                        "B Rating": br,
                        "Win %": edge,
                    })

                pair_df = pd.DataFrame(pair_rows)
                st.dataframe(pair_df, hide_index=True, use_container_width=True,
                             column_config={"  ": st.column_config.TextColumn(width="small")})

                st.markdown("")
                btn_col, _ = st.columns([1, 3])
                with btn_col:
                    if st.button(f"🎲 Simulate Round {current_round + 1}",
                                  type="primary", use_container_width=True):
                        results = simulate_round(pending)
                        apply_results(results)
                        st.session_state.rounds.append({"round_num": current_round + 1, "pairings": results})
                        st.session_state.current_round += 1

                        if st.session_state.current_round >= total_rounds:
                            st.session_state.tournament_done    = True
                            st.session_state.pending_pairings   = []
                        else:
                            st.session_state.pending_pairings = make_pairings(st.session_state.players)

                        st.rerun()

        else:
            st.success("## 🏆 Tournament Complete!")
            # Apply rating changes once when tournament completes
            if not st.session_state.get("ratings_applied", False):
                rating_changes = compute_and_apply_rating_changes()
                st.session_state.rating_changes = rating_changes
                # Save tournament to history
                try:
                    tourn_data = get_tournament_data()
                    save_tournament(tourn_data)
                    st.success("✅ Tournament saved to history!")
                except Exception as e:
                    st.warning(f"Could not save tournament: {e}")
            else:
                rating_changes = st.session_state.get("rating_changes", {})

            standings = get_standings()
            if standings:
                winner = standings[0]
                st.markdown(f"### 🥇 Winner: **{winner['Name']}** — {winner['Score']} pts")
            if rating_changes:
                st.markdown("**Rating changes**")
                rc_rows = []
                for n, info in rating_changes.items():
                    sign = "+" if info["delta"] >= 0 else ""
                    rc_rows.append({"Name": n, "Old": info["old"], "Change": f"{sign}{info['delta']}", "New": info["new"]})
                st.dataframe(pd.DataFrame(rc_rows), hide_index=True, use_container_width=True)

        # ── Completed rounds ──────────────────────────────────────────────────
        if st.session_state.rounds:
            st.markdown("---")
            st.markdown("### Results by Round")

            for rnd in reversed(st.session_state.rounds):
                rnum = rnd["round_num"]
                with st.expander(f"Round {rnum} Results", expanded=(rnum == current_round)):
                    r_rows = []
                    for p in rnd["pairings"]:
                        wn, bn = p["white"], p["black"]
                        res    = p.get("result", "—")
                        wr     = get_player(wn)["rating"] if get_player(wn) else "—"
                        br     = get_player(bn)["rating"] if bn != "BYE" and get_player(bn) else "—"

                        # Annotate result
                        if res == "1-0":    label = f"✅ {wn} wins"
                        elif res == "0-1":  label = f"✅ {bn} wins"
                        elif res == "½-½":  label = "🟡 Draw"
                        elif res == "1-BYE":label = f"🔵 {wn} (bye)"
                        else:               label = res

                        r_rows.append({
                            "White": wn,
                            "W Rtg": wr,
                            "Result": res,
                            "Black": bn,
                            "B Rtg": br,
                            "Summary": label,
                        })

                    r_df = pd.DataFrame(r_rows)
                    st.dataframe(r_df, hide_index=True, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 – STANDINGS
# ════════════════════════════════════════════════════════════════════════════════
with tab3:

    if not st.session_state.players:
        st.info("No players registered yet.")
    elif st.session_state.current_round == 0:
        st.info("No results yet. Head to **Tournament** to simulate rounds.")
    else:
        standings = get_standings()
        st.markdown(f'<div class="section-header">Standings after Round {st.session_state.current_round}</div>',
                    unsafe_allow_html=True)

        s_rows = []
        # Build name->rank map from final standings
        name_to_rank = {s["Name"]: idx + 1 for idx, s in enumerate(standings)}
        for i, s in enumerate(standings):
            p = s["_player"]
            # Build result history string using W/L/D + opponent final standing number
            hist = []
            for opp, sc in p["results"]:
                if opp == "BYE":
                    hist.append("BYE")
                    continue
                opp_rank = name_to_rank.get(opp)
                rank_str = str(opp_rank) if opp_rank is not None else "?"
                if sc == 1.0:
                    hist.append(f"W{rank_str}")
                elif sc == 0.5:
                    hist.append(f"D{rank_str}")
                elif sc == 0.0:
                    hist.append(f"L{rank_str}")
                else:
                    hist.append(f"?{rank_str}")

            s_rows.append({
                "Rank": rank_medal(i),
                "Name": s["Name"],
                "Rating": s["Rating"],
                "Score": s["Score"],
                "Buchholz": s["Buchholz"],
                "Results": " ".join(hist),
            })

        stand_df = pd.DataFrame(s_rows)
        st.dataframe(
            stand_df,
            hide_index=True,
            use_container_width=True,
            height=min(50 + len(s_rows) * 36, 600),
            column_config={
                "Score": st.column_config.NumberColumn(format="%.1f"),
                "Buchholz": st.column_config.NumberColumn(format="%.1f"),
                "Results": st.column_config.TextColumn(width="medium"),
            },
        )

        # ── Per-player detail ─────────────────────────────────────────────────
        st.markdown("---")
        st.markdown('<div class="section-header">Player Detail</div>', unsafe_allow_html=True)

        sel = st.selectbox("Select player to inspect",
                           options=[p["name"] for p in st.session_state.players])
        if sel:
            player = get_player(sel)
            if player and player["results"]:
                detail_rows = []
                running = 0.0
                for rnd_idx, (opp, sc) in enumerate(player["results"]):
                    running += sc
                    color = player["colors"][rnd_idx] if rnd_idx < len(player["colors"]) else "—"
                    opp_rating = get_player(opp)["rating"] if opp != "BYE" and get_player(opp) else "—"
                    # Find opponent final rank if available
                    standings = get_standings()
                    name_to_rank = {s["Name"]: idx + 1 for idx, s in enumerate(standings)}
                    opp_rank = name_to_rank.get(opp)
                    if opp == "BYE":
                        res_label = "BYE"
                    elif sc == 1.0:
                        res_label = f"W{opp_rank if opp_rank else '?'}"
                    elif sc == 0.5:
                        res_label = f"D{opp_rank if opp_rank else '?'}"
                    elif sc == 0.0:
                        res_label = f"L{opp_rank if opp_rank else '?'}"
                    else:
                        res_label = score_str(sc)

                    detail_rows.append({
                        "Rnd": rnd_idx + 1,
                        "Color": "⬜ White" if color == "W" else "⬛ Black",
                        "Opponent": opp,
                        "Opp Rtg": opp_rating,
                        "Result": res_label,
                        "Points": score_str(sc),
                        "Running": f"{running:.1f}",
                    })

                st.dataframe(pd.DataFrame(detail_rows), hide_index=True, use_container_width=True)

                # Score progression mini chart
                st.markdown(f"**Score progression** for {sel}")
                chart_data = pd.DataFrame({
                    "Round": list(range(1, len(player["cumulative"]) + 1)),
                    "Score": player["cumulative"],
                })
                st.line_chart(chart_data.set_index("Round"), use_container_width=True, height=180)
            else:
                st.info("No results yet for this player.")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 4 – TOURNAMENT HISTORY
# ════════════════════════════════════════════════════════════════════════════════
with tab4:
    tournaments = load_tournaments()
    
    if not tournaments:
        st.info("No tournament history yet. Complete a tournament to see it here.")
    else:
        st.markdown(f'<div class="section-header">Previous Tournaments ({len(tournaments)})</div>',
                    unsafe_allow_html=True)
        
        # Reverse order so newest tournaments appear first
        for idx, tourn in enumerate(reversed(tournaments)):
            timestamp = tourn.get("timestamp", "Unknown")
            num_players = tourn.get("num_players", 0)
            num_rounds = tourn.get("num_rounds", 0)
            
            # Get winner
            standings = tourn.get("standings", [])
            winner = standings[0]["name"] if standings else "Unknown"
            winner_score = standings[0]["score"] if standings else 0
            
            with st.expander(f"🏆 {timestamp} · {num_players} players · {num_rounds} rounds · Winner: **{winner}** ({winner_score}pts)"):
                col1, col2 = st.columns([1, 1])
                
                with col1:
                    st.markdown("**Initial Ratings**")
                    init_df = pd.DataFrame(tourn.get("players_initial", []))
                    st.dataframe(init_df, hide_index=True, use_container_width=True, height=200)
                
                with col2:
                    st.markdown("**Rating Changes**")
                    rating_changes = tourn.get("rating_changes", {})
                    if rating_changes:
                        rc_display = []
                        for name, info in rating_changes.items():
                            sign = "+" if info["delta"] >= 0 else ""
                            rc_display.append({
                                "Player": name,
                                "Change": f"{sign}{info['delta']}",
                                "New Rating": info["new"],
                            })
                        rc_df = pd.DataFrame(rc_display)
                        st.dataframe(rc_df, hide_index=True, use_container_width=True, height=200)
                    else:
                        st.text("No rating changes recorded")
                
                st.markdown("---")
                st.markdown("**Final Standings**")
                
                final_df = pd.DataFrame([
                    {
                        "Rank": rank_medal(s["rank"] - 1),
                        "Player": s["name"],
                        "Final Rating": s["rating"],
                        "Score": s["score"],
                        "Buchholz": s["buchholz"],
                    }
                    for s in standings
                ])
                
                st.dataframe(
                    final_df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Score": st.column_config.NumberColumn(format="%.1f"),
                        "Buchholz": st.column_config.NumberColumn(format="%.1f"),
                    },
                )
        
        st.markdown("---")
        st.markdown('<div class="section-header">Player Statistics (All Tournaments)</div>',
                    unsafe_allow_html=True)
        
        # Get all unique players across all tournaments
        all_players = set()
        for tourn in tournaments:
            for player in tourn.get("standings", []):
                all_players.add(player["name"])
        
        all_players = sorted(list(all_players))
        
        if all_players:
            selected_player = st.selectbox("Select player to view opponent record",
                                           options=all_players,
                                           key="history_player_select")
            
            if selected_player:
                opponent_stats = aggregate_player_stats(selected_player)
                
                if opponent_stats:
                    # Build display table
                    stats_rows = []
                    for rank, (opponent, stats) in enumerate(sorted(opponent_stats.items(),
                                                                     key=lambda x: x[1]["games"],
                                                                     reverse=True), 1):
                        games = stats["games"]
                        wins = stats["wins"]
                        draws = stats["draws"]
                        losses = stats["losses"]
                        win_pct = (wins / games * 100) if games > 0 else 0
                        
                        stats_rows.append({
                            "Rank": rank,
                            "Opponent": opponent,
                            "Games": games,
                            "Win": wins,
                            "Draw": draws,
                            "Loss": losses,
                            "Win %": f"{win_pct:.1f}",
                        })
                    
                    stats_df = pd.DataFrame(stats_rows)
                    st.dataframe(
                        stats_df,
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "Games": st.column_config.NumberColumn(),
                            "Win": st.column_config.NumberColumn(),
                            "Draw": st.column_config.NumberColumn(),
                            "Loss": st.column_config.NumberColumn(),
                        },
                    )
                    
                    # Summary stats
                    total_games = sum(s["games"] for s in opponent_stats.values())
                    total_wins = sum(s["wins"] for s in opponent_stats.values())
                    total_draws = sum(s["draws"] for s in opponent_stats.values())
                    total_losses = sum(s["losses"] for s in opponent_stats.values())
                    overall_win_pct = (total_wins / total_games * 100) if total_games > 0 else 0
                    
                    st.markdown("---")
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Total Games", total_games)
                    with col2:
                        st.metric("Wins", total_wins)
                    with col3:
                        st.metric("Draws", total_draws)
                    with col4:
                        st.metric("Losses", total_losses)
                    
                    st.metric("Overall Win %", f"{overall_win_pct:.1f}%")
                else:
                    st.info(f"{selected_player} has no recorded games in tournament history.")
        else:
            st.info("No players found in tournament history.")