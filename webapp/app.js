"use strict";

/**
 * The Mini App.
 *
 * This file draws screens and calls the bot. It holds no Korail knowledge:
 * the station lists, the train lists, what counts as a valid time window and
 * whether a search may start are all the server's answers, fetched over the
 * API. That is deliberate - the railway logic exists once, in Python, and a
 * second copy here would be a second copy to keep correct.
 *
 * Everything is authenticated by Telegram's initData, sent as a header on
 * every request. The server takes the chat ID from its signature, so nothing
 * this file sends can act as somebody else.
 */

const tg = window.Telegram?.WebApp;
const el = (id) => document.getElementById(id);

/** Screens, and the stack that the back button walks. */
const VIEWS = ["home", "conditions", "trains", "confirm", "register", "settings", "message"];
const history = [];

/** Everything the server told us at launch, refreshed after anything changes. */
let state = null;
/** The booking being put together, kept here so a back press loses nothing. */
let draft = { trains: [] };
/** Which railway a registration screen is collecting an account for. */
let registerFor = "korail";

// ============================================================
// Talking to the bot
// ============================================================

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(path, { method = "POST", body } = {}) {
  const initData = tg?.initData || "";
  if (!initData) {
    throw new ApiError("텔레그램에서 열어야 사용할 수 있는 화면입니다.", 401);
  }

  let response;
  try {
    response = await fetch(`api${path}`, {
      method,
      headers: {
        "X-Telegram-Init-Data": initData,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("봇에 연결하지 못했습니다. 네트워크를 확인해주세요.", 0);
  }

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(payload.error || "요청을 처리하지 못했습니다.", response.status);
  }
  return payload;
}

/** Run something that talks to the bot, with the spinner and one error path. */
async function guarded(errorBox, work) {
  if (errorBox) errorBox.textContent = "";
  el("blocker").hidden = false;
  try {
    return await work();
  } catch (error) {
    const message = error instanceof Error ? error.message : "문제가 생겼습니다.";
    tg?.HapticFeedback?.notificationOccurred("error");
    if (errorBox) {
      errorBox.textContent = message;
      errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
    } else {
      showMessage("문제가 생겼습니다", message);
    }
    return undefined;
  } finally {
    el("blocker").hidden = true;
  }
}

// ============================================================
// Navigation
// ============================================================

function show(name, { replace = false } = {}) {
  if (!replace && history[history.length - 1] !== name) history.push(name);
  for (const view of VIEWS) el(`view-${view}`).hidden = view !== name;
  window.scrollTo({ top: 0 });

  if (history.length > 1 && tg?.BackButton) tg.BackButton.show();
  else tg?.BackButton?.hide();
}

function back() {
  history.pop();
  const previous = history[history.length - 1] || "home";
  show(previous, { replace: true });
  if (previous === "home") refreshHome();
}

function showMessage(title, body, onDismiss) {
  el("message-title").textContent = title;
  el("message-body").textContent = body;
  el("message-action").onclick = () => {
    if (onDismiss) onDismiss();
    else goHome();
  };
  show("message");
}

function goHome() {
  history.length = 0;
  show("home");
  refreshHome();
}

// ============================================================
// Home
// ============================================================

async function refreshHome() {
  const fresh = await guarded(null, () => api("/bootstrap"));
  if (fresh) {
    state = fresh;
    renderHome();
  }
}

function renderHome() {
  const running = state.running;
  el("running-card").hidden = !running;
  if (running) {
    el("running-route").textContent = `${running.srcLocate} → ${running.dstLocate}`;
    el("running-meta").textContent = [
      formatDate(running.depDate),
      `${formatClock(running.depTime)}~${formatClock(running.maxDepTime)}`,
      `${running.passengerCount}명`,
      running.selectedTrains.length
        ? `${running.selectedTrains.length}개 열차 감시`
        : "시간대 전체 감시",
    ].join(" · ");
  }

  const scheduled = state.scheduled;
  el("scheduled-card").hidden = !scheduled;
  if (scheduled) {
    el("scheduled-route").textContent =
      `${scheduled.search.srcLocate} → ${scheduled.search.dstLocate}`;
    el("scheduled-meta").textContent = `${formatStamp(scheduled.startAt)} 에 시작`;
  }

  const pending = state.pending || [];
  el("pending-card").hidden = pending.length === 0;
  if (pending.length) {
    const list = el("pending-list");
    list.replaceChildren();
    for (const item of pending) {
      const row = document.createElement("p");
      row.className = "status-meta";
      row.textContent = [
        item.seatNumber !== null && item.seatNumber !== undefined
          ? `좌석 ${item.seatNumber}`
          : null,
        item.trainInfo,
        item.reservationId ? `예약번호 ${item.reservationId}` : null,
        item.expiresAt ? `${formatStamp(item.expiresAt)} 까지 결제` : null,
      ]
        .filter(Boolean)
        .join("\n");
      list.append(row);
    }
    const operator = state.running?.operator || "korail";
    el("pay-link").href = state.paymentUrls[operator] || state.paymentUrls.korail;
  }

  const favourites = state.favourites || [];
  el("favourites-card").hidden = favourites.length === 0;
  const list = el("favourites-list");
  list.replaceChildren();
  for (const favourite of favourites) {
    list.append(
      row(favourite.name, `${favourite.route} · ${favourite.window}`, [
        chip("시작", () => startFromFavourite(favourite)),
        chip("삭제", () => removeFavourite(favourite), true),
      ]),
    );
  }

  el("new-search").textContent = running ? "다른 조건으로 새 예약" : "새 예약 시작";
  el("version").textContent = `v${state.version}`;
}

function row(title, sub, actions) {
  const wrapper = document.createElement("div");
  wrapper.className = "row";
  const left = document.createElement("div");
  const heading = document.createElement("div");
  heading.className = "title";
  heading.textContent = title;
  const detail = document.createElement("div");
  detail.className = "sub";
  detail.textContent = sub;
  left.append(heading, detail);
  const right = document.createElement("div");
  right.className = "actions";
  right.append(...actions);
  wrapper.append(left, right);
  return wrapper;
}

function chip(label, onClick, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = danger ? "chip-button danger" : "chip-button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

// ============================================================
// Conditions
// ============================================================

function operatorState() {
  return state.operators[selectedOperator()];
}

function selectedOperator() {
  return el("conditions-form").elements.operator.value || "korail";
}

function renderStations() {
  const operator = operatorState();
  const source = el("src-station");
  const destination = el("dst-station");
  const known = new Set(operator.stations);
  if (!known.has(source.value)) source.value = "";
  if (!known.has(destination.value)) destination.value = "";

  const options = el("station-options");
  options.replaceChildren();
  for (const station of operator.stations) {
    const option = document.createElement("option");
    option.value = station;
    options.append(option);
  }

  const quick = el("quick-stations");
  quick.replaceChildren();
  for (const station of operator.majorStations) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = station;
    button.addEventListener("click", () => {
      const target = source.value ? destination : source;
      target.value = station;
    });
    quick.append(button);
  }

  el("train-type-group").hidden = selectedOperator() === "srt";
  // Korail does not say which seat a booking got until the ticket is paid
  // for, and this bot never pays - so there is nothing to check a condition
  // against, and offering one would be offering something that does nothing.
  el("seat-preference-group").hidden = selectedOperator() !== "srt";

  const hint = el("operator-hint");
  hint.hidden = operator.registered;
  hint.textContent = operator.registered
    ? ""
    : `${operator.displayName} 계정이 아직 등록되어 있지 않습니다. 열차를 찾을 때 등록 화면으로 안내합니다.`;
}

function setPassengerCount(next) {
  const count = Math.min(9, Math.max(1, next));
  el("passenger-count").value = String(count);
  el("passenger-output").value = `${count}명`;
  el("seat-strategy-group").hidden = count === 1;
}

function readConditions() {
  const form = el("conditions-form");
  const data = new FormData(form);
  const compact = (value) => String(value || "").replaceAll("-", "").replaceAll(":", "");
  const unlimited = el("unlimited-time").checked;

  return {
    v: 1,
    action: "prepare_search",
    operator: selectedOperator(),
    dep_date: compact(el("dep-date").value),
    src_station: String(data.get("src_station") || "").trim(),
    dst_station: String(data.get("dst_station") || "").trim(),
    dep_time: compact(data.get("dep_time")),
    max_dep_time: unlimited ? "2400" : compact(data.get("max_dep_time")),
    train_type: String(data.get("train_type") || "1"),
    seat_option: String(data.get("seat_option") || "1"),
    passenger_count: Number(el("passenger-count").value || 1),
    seat_strategy: String(data.get("seat_strategy") || "1"),
    seat_preference: readSeatPreference(),
  };
}

// "A,D:1-15", the same flattening SeatPreference.encode does on the bot side.
// One representation across the page, the API and the search process, so
// there is a single place the shape can be got wrong.
function readSeatPreference() {
  if (selectedOperator() !== "srt") return "";

  const columns = [...document.querySelectorAll("input[name='seat_column']:checked")]
    .map((box) => box.value)
    .join(",");
  const low = String(el("seat-row-min").value || "").trim();
  const high = String(el("seat-row-max").value || "").trim();

  if (!columns && !low && !high) return "";
  return `${columns}:${low || high ? `${low}-${high}` : ""}`;
}

function applySeatPreference(encoded) {
  const [columnPart = "", rowPart = ""] = String(encoded || "").split(":");
  const wanted = new Set(columnPart.split(",").filter(Boolean));
  for (const box of document.querySelectorAll("input[name='seat_column']")) {
    box.checked = wanted.has(box.value);
  }
  const [low = "", high = ""] = rowPart.split("-");
  el("seat-row-min").value = low;
  el("seat-row-max").value = high;
}

function describeSeatPreference(encoded) {
  const [columnPart = "", rowPart = ""] = String(encoded || "").split(":");
  const columns = columnPart.split(",").filter(Boolean);
  const [low = "", high = ""] = rowPart.split("-");

  const parts = [];
  if (columns.length) parts.push(`${columns.join("·")}열`);
  if (low && high) parts.push(`${low}~${high}번`);
  else if (low) parts.push(`${low}번 이상`);
  else if (high) parts.push(`${high}번 이하`);
  return parts.length ? parts.join(" ") : "지정 없음";
}

function applyConditions(conditions) {
  if (!conditions) return;
  const form = el("conditions-form");
  form.elements.operator.value = conditions.operator || "korail";
  renderStations();
  el("src-station").value = conditions.src_station || "";
  el("dst-station").value = conditions.dst_station || "";
  if (conditions.dep_date && conditions.dep_date.length === 8) {
    el("dep-date").value =
      `${conditions.dep_date.slice(0, 4)}-${conditions.dep_date.slice(4, 6)}-${conditions.dep_date.slice(6, 8)}`;
  }
  if (conditions.dep_time) el("dep-time").value = formatClock(conditions.dep_time);
  const unlimited = conditions.max_dep_time === "2400";
  el("unlimited-time").checked = unlimited;
  el("max-dep-time").disabled = unlimited;
  if (!unlimited && conditions.max_dep_time) {
    el("max-dep-time").value = formatClock(conditions.max_dep_time);
  }
  for (const [name, value] of [
    ["train_type", conditions.train_type],
    ["seat_option", conditions.seat_option],
    ["seat_strategy", conditions.seat_strategy],
  ]) {
    if (value && form.elements[name]) form.elements[name].value = String(value);
  }
  setPassengerCount(Number(conditions.passenger_count || 1));
  applySeatPreference(conditions.seat_preference);
  draft.trains = Array.isArray(conditions.trains) ? conditions.trains.map(String) : [];
  renderStations();
}

async function findTrains() {
  draft.conditions = readConditions();
  const result = await guarded(el("conditions-error"), () =>
    api("/trains", { body: { conditions: draft.conditions } }),
  );
  if (!result) return;

  draft.trains = draft.trains.filter((number) =>
    result.trains.some((train) => train.no === number),
  );
  renderTrains(result);
  show("trains");
}

// ============================================================
// Train picker
// ============================================================

function renderTrains(result) {
  const conditions = draft.conditions;
  el("trains-intro").textContent =
    `${conditions.src_station} → ${conditions.dst_station} · ` +
    `${formatDate(conditions.dep_date)} ${formatClock(conditions.dep_time)}부터`;

  const list = el("train-list");
  list.replaceChildren();

  if (!result.trains.length) {
    const empty = document.createElement("p");
    empty.className = "empty-line";
    empty.textContent = "이 시간대에 조회된 열차가 없습니다. 시간대 전체를 감시할 수 있습니다.";
    list.append(empty);
  }

  for (const train of result.trains) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "train";
    button.setAttribute("aria-pressed", String(draft.trains.includes(train.no)));

    const tick = document.createElement("span");
    tick.className = "tick";
    tick.textContent = "✓";

    const label = document.createElement("span");
    label.className = "label";
    label.textContent = train.label;

    const seat = document.createElement("span");
    seat.className = train.soldout ? "seat soldout" : "seat";
    seat.textContent = train.soldout ? "매진" : "여석 있음";

    button.append(tick, label, seat);
    button.addEventListener("click", () => {
      const index = draft.trains.indexOf(train.no);
      if (index === -1) draft.trains.push(train.no);
      else draft.trains.splice(index, 1);
      button.setAttribute("aria-pressed", String(index === -1));
      tg?.HapticFeedback?.selectionChanged();
      updateTrainsNext();
    });
    list.append(button);
  }

  el("trains-note").hidden = result.trains.length === 0;
  updateTrainsNext();
}

function updateTrainsNext() {
  el("trains-next").textContent = draft.trains.length
    ? `${draft.trains.length}개 열차로 계속`
    : "시간대 전체 감시";
}

async function refreshTrains() {
  const result = await guarded(el("trains-error"), () =>
    api("/trains", { body: { conditions: draft.conditions } }),
  );
  if (!result) return;
  draft.trains = draft.trains.filter((number) =>
    result.trains.some((train) => train.no === number),
  );
  renderTrains(result);
}

// ============================================================
// Confirmation
// ============================================================

const SEAT_OPTIONS = { 1: "일반실 우선", 2: "일반실만", 3: "특실 우선", 4: "특실만" };

function renderSummary() {
  const conditions = draft.conditions;
  const rows = [
    ["철도", state.operators[conditions.operator].displayName],
    ["구간", `${conditions.src_station} → ${conditions.dst_station}`],
    ["날짜", formatDate(conditions.dep_date)],
    [
      "시간대",
      conditions.max_dep_time === "2400"
        ? `${formatClock(conditions.dep_time)} 이후 전체`
        : `${formatClock(conditions.dep_time)} ~ ${formatClock(conditions.max_dep_time)}`,
    ],
    ["좌석", SEAT_OPTIONS[conditions.seat_option] || "일반실 우선"],
    ["인원", `${conditions.passenger_count}명`],
  ];

  if (conditions.operator === "korail") {
    rows.splice(4, 0, ["열차", conditions.train_type === "1" ? "KTX 계열만" : "모든 열차"]);
  }
  if (Number(conditions.passenger_count) > 1) {
    rows.push(["좌석 배치", conditions.seat_strategy === "1" ? "연속 좌석" : "랜덤 배치"]);
  }
  if (conditions.operator === "srt" && conditions.seat_preference) {
    rows.push(["좌석 지정", describeSeatPreference(conditions.seat_preference)]);
  }
  rows.push([
    "감시 범위",
    draft.trains.length ? `${draft.trains.join(", ")}번 열차` : "시간대 전체",
  ]);

  const summary = el("summary");
  summary.replaceChildren();
  for (const [term, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = value;
    summary.append(dt, dd);
  }
}

function bookingBody() {
  return { conditions: draft.conditions, trains: draft.trains };
}

async function startNow() {
  const result = await guarded(el("confirm-error"), () =>
    api("/search", { body: bookingBody() }),
  );
  if (!result) return;

  if (result.needsAccessRequest) {
    offerAccessRequest(result);
    return;
  }

  tg?.HapticFeedback?.notificationOccurred("success");
  const trial =
    result.trialUsed !== null && result.trialUsed !== undefined
      ? `\n\n체험 ${result.trialUsed}/${result.trialLimit}회 사용했습니다.`
      : "";
  showMessage(
    "검색을 시작했습니다",
    `좌석이 나오면 대화창으로 알려드립니다. 앱을 닫아도 검색은 계속됩니다.${trial}`,
  );
}

function offerAccessRequest(result) {
  const asked = result.accessRequestPending;
  el("message-title").textContent = "체험 횟수를 모두 사용했습니다";
  el("message-body").textContent = asked
    ? `${result.trialUsed}/${result.trialLimit}회를 사용했습니다. 이미 사용 승인을 요청해두었고, 운영자의 답을 기다리는 중입니다.`
    : `${result.trialUsed}/${result.trialLimit}회를 사용했습니다. 운영자에게 사용 승인을 요청할 수 있습니다.`;
  el("message-action").textContent = asked ? "확인" : "사용 승인 요청하기";
  el("message-action").onclick = asked
    ? goHome
    : async () => {
        const sent = await guarded(null, () => api("/access-request"));
        if (sent) showMessage("요청을 보냈습니다", "운영자가 승인하면 대화창으로 알려드립니다.");
      };
  show("message");
}

async function scheduleSearch() {
  const value = el("schedule-at").value;
  if (!value) {
    el("confirm-error").textContent = "검색을 시작할 시각을 골라주세요.";
    return;
  }
  const result = await guarded(el("confirm-error"), () =>
    api("/schedule", { body: { ...bookingBody(), start_at: value } }),
  );
  if (!result) return;

  tg?.HapticFeedback?.notificationOccurred("success");
  showMessage(
    "시작 시각을 예약했습니다",
    `${formatStamp(result.startAt)} 에 검색을 시작합니다. 앱을 닫아도 됩니다.`,
  );
}

async function saveFavourite() {
  const result = await guarded(el("confirm-error"), () =>
    api("/favourites", { body: { conditions: draft.conditions } }),
  );
  if (!result) return;
  state.favourites = result.favourites;
  tg?.HapticFeedback?.notificationOccurred("success");
  el("save-favourite").textContent = "저장됨";
}

// ============================================================
// Favourites
// ============================================================

function startFromFavourite(favourite) {
  applyConditions(favourite.conditions);
  // The date is deliberately not part of a favourite - a route saved in March
  // is not a trip in March - so the picker opens on the saved conditions with
  // the date still to choose.
  el("dep-date").value = el("dep-date").value || localDate(1);
  show("conditions");
}

async function removeFavourite(favourite) {
  const result = await guarded(null, () =>
    api(`/favourites/${encodeURIComponent(favourite.id)}`, { method: "DELETE" }),
  );
  if (!result) return;
  state.favourites = result.favourites;
  renderHome();
}

// ============================================================
// Registration
// ============================================================

function askToRegister(operator) {
  registerFor = operator;
  el("register-title").textContent = `${state.operators[operator].displayName} 계정 등록`;
  el("register-error").textContent = "";
  el("register-password").value = "";
  show("register");
}

async function register(event) {
  event.preventDefault();
  const result = await guarded(el("register-error"), () =>
    api("/register", {
      body: {
        operator: registerFor,
        username: el("register-username").value,
        password: el("register-password").value,
      },
    }),
  );
  if (!result) return;

  el("register-password").value = "";
  state.operators[registerFor].registered = true;
  tg?.HapticFeedback?.notificationOccurred("success");

  // Straight back to what the registration was for, rather than to the home
  // screen: this is reached mid-booking, and dropping the answers already
  // given would make registering feel like starting over.
  back();
  if (history[history.length - 1] === "conditions") {
    renderStations();
    findTrains();
  }
}

// ============================================================
// Settings
// ============================================================

const NOTIFY_STEPS = [0, 1, 3, 5, 10, 15, 30, 60, 120, 180];

function renderSettings() {
  el("notify-output").value = state.notifyMinutes ? `${state.notifyMinutes}분` : "끔";

  const list = el("accounts-list");
  list.replaceChildren();
  for (const [key, operator] of Object.entries(state.operators)) {
    list.append(
      row(
        operator.displayName,
        operator.registered ? "등록됨" : "등록되지 않음",
        operator.registered
          ? [chip("해제", () => logout(key), true)]
          : [chip("등록", () => askToRegister(key))],
      ),
    );
  }
}

async function stepNotify(direction) {
  const current = NOTIFY_STEPS.indexOf(state.notifyMinutes);
  const index = Math.min(
    NOTIFY_STEPS.length - 1,
    Math.max(0, (current === -1 ? 3 : current) + direction),
  );
  const result = await guarded(el("settings-error"), () =>
    api("/notify", { body: { minutes: NOTIFY_STEPS[index] } }),
  );
  if (!result) return;
  state.notifyMinutes = result.notifyMinutes;
  renderSettings();
}

async function logout(operator) {
  const result = await guarded(el("settings-error"), () =>
    api("/logout", { body: { operator } }),
  );
  if (!result) return;
  state.operators[operator].registered = false;
  renderSettings();
}

// ============================================================
// Stopping things
// ============================================================

async function stopSearch() {
  const result = await guarded(null, () => api("/search/cancel"));
  if (!result) return;
  tg?.HapticFeedback?.notificationOccurred("success");
  await refreshHome();
}

async function cancelPending() {
  const result = await guarded(null, () => api("/reservations/cancel"));
  if (!result) return;
  tg?.HapticFeedback?.notificationOccurred("success");
  showMessage("예약을 취소했습니다", "철도사가 취소를 확인했습니다.");
}

// ============================================================
// Formatting
// ============================================================

function localDate(offsetDays) {
  const date = new Date();
  date.setHours(12, 0, 0, 0);
  date.setDate(date.getDate() + offsetDays);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function formatDate(compact) {
  if (!compact || compact.length !== 8) return compact || "";
  return `${Number(compact.slice(4, 6))}월 ${Number(compact.slice(6, 8))}일`;
}

function formatClock(value) {
  const digits = String(value || "").replaceAll(":", "");
  if (digits.length < 4) return digits;
  return `${digits.slice(0, 2)}:${digits.slice(2, 4)}`;
}

function formatStamp(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${month}월 ${day}일 ${hour}:${minute}`;
}

// ============================================================
// Wiring
// ============================================================

el("new-search").addEventListener("click", () => {
  draft = { trains: [] };
  el("conditions-error").textContent = "";
  applyConditions(state.draft || { operator: "korail" });
  if (!el("dep-date").value) el("dep-date").value = localDate(1);
  show("conditions");
});
el("refresh-home").addEventListener("click", refreshHome);
el("open-settings").addEventListener("click", () => {
  renderSettings();
  show("settings");
});
el("stop-search").addEventListener("click", stopSearch);
el("drop-schedule").addEventListener("click", stopSearch);
el("cancel-pending").addEventListener("click", cancelPending);

el("operator-picker").addEventListener("change", renderStations);
el("swap-stations").addEventListener("click", () => {
  const source = el("src-station");
  const destination = el("dst-station");
  [source.value, destination.value] = [destination.value, source.value];
});
el("unlimited-time").addEventListener("change", (event) => {
  el("max-dep-time").disabled = event.target.checked;
});
el("passenger-minus").addEventListener("click", () =>
  setPassengerCount(Number(el("passenger-count").value) - 1),
);
el("passenger-plus").addEventListener("click", () =>
  setPassengerCount(Number(el("passenger-count").value) + 1),
);
el("conditions-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const operator = operatorState();
  if (!operator.registered) {
    draft.conditions = readConditions();
    askToRegister(selectedOperator());
    return;
  }
  findTrains();
});

el("refresh-trains").addEventListener("click", refreshTrains);
el("trains-next").addEventListener("click", () => {
  el("confirm-error").textContent = "";
  el("save-favourite").textContent = "즐겨찾기 저장";
  el("schedule-card").hidden = true;
  renderSummary();
  show("confirm");
});

el("start-now").addEventListener("click", startNow);
el("open-schedule").addEventListener("click", () => {
  const card = el("schedule-card");
  card.hidden = !card.hidden;
  if (!card.hidden && !el("schedule-at").value) {
    el("schedule-at").value = `${localDate(0)}T07:00`;
  }
});
el("confirm-schedule").addEventListener("click", scheduleSearch);
el("save-favourite").addEventListener("click", saveFavourite);

el("register-form").addEventListener("submit", register);
el("notify-minus").addEventListener("click", () => stepNotify(-1));
el("notify-plus").addEventListener("click", () => stepNotify(1));

// ============================================================
// Launch
// ============================================================

async function launch() {
  el("dep-date").min = localDate(0);
  el("dep-date").max = localDate(365);
  el("dep-date").value = localDate(1);

  if (tg) {
    tg.ready();
    tg.expand();
    tg.disableVerticalSwipes?.();
    tg.BackButton.onClick(back);
  }

  try {
    state = await api("/bootstrap");
  } catch (error) {
    el("boot").textContent =
      error instanceof Error ? error.message : "봇에 연결하지 못했습니다.";
    return;
  }

  el("boot").hidden = true;
  renderHome();
  show("home");
}

launch();
