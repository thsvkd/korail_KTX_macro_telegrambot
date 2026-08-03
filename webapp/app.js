"use strict";

const STATIONS = {
  korail: ["서울", "용산", "청량리", "광명", "천안아산", "오송", "대전", "동대구", "부산", "울산(통도사)", "포항", "익산", "전주", "광주송정", "목포", "여수EXPO", "강릉"],
  srt: ["수서", "동탄", "평택지제", "천안아산", "오송", "대전", "동대구", "부산", "울산(통도사)", "포항", "광주송정", "목포", "익산", "전주", "여수EXPO", "창원중앙", "진주", "경주"],
};

const form = document.querySelector("#reservation-form");
const operatorPicker = document.querySelector("#operator-picker");
const stationOptions = document.querySelector("#station-options");
const quickStations = document.querySelector("#quick-stations");
const srcStation = document.querySelector("#src-station");
const dstStation = document.querySelector("#dst-station");
const depDate = document.querySelector("#dep-date");
const unlimitedTime = document.querySelector("#unlimited-time");
const maxDepTime = document.querySelector("#max-dep-time");
const trainTypeGroup = document.querySelector("#train-type-group");
const passengerCount = document.querySelector("#passenger-count");
const passengerOutput = document.querySelector("#passenger-output");
const seatStrategyGroup = document.querySelector("#seat-strategy-group");
const errorBox = document.querySelector("#form-error");
const preview = document.querySelector("#browser-preview");
const submitButton = document.querySelector("#submit-button");
const tg = window.Telegram?.WebApp;
const launchParams = new URLSearchParams(location.search);
const startTransport = launchParams.get("transport") === "start";
const botUsername = (launchParams.get("bot") || "").replace(/^@/, "");

function localDate(offsetDays) {
  const date = new Date();
  date.setHours(12, 0, 0, 0);
  date.setDate(date.getDate() + offsetDays);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function selectedOperator() {
  return form.elements.operator.value || "korail";
}

function configureOperators() {
  for (const label of operatorPicker.querySelectorAll("[data-operator]")) {
    label.hidden = false;
    label.querySelector("input").disabled = false;
  }
  form.elements.operator.value = "korail";
  operatorPicker.style.gridTemplateColumns = "1fr 1fr";
}

function renderStations() {
  const operator = selectedOperator();
  if (!STATIONS[operator].includes(srcStation.value)) srcStation.value = "";
  if (!STATIONS[operator].includes(dstStation.value)) dstStation.value = "";
  stationOptions.replaceChildren();
  quickStations.replaceChildren();
  for (const station of STATIONS[operator]) {
    const option = document.createElement("option");
    option.value = station;
    stationOptions.append(option);
  }
  for (const station of STATIONS[operator].slice(0, 8)) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = station;
    button.addEventListener("click", () => {
      const target = srcStation.value ? dstStation : srcStation;
      target.value = station;
      target.focus();
    });
    quickStations.append(button);
  }
  trainTypeGroup.hidden = operator === "srt";
}

function setPassengerCount(next) {
  const count = Math.min(9, Math.max(1, next));
  passengerCount.value = String(count);
  passengerOutput.value = `${count}명`;
  seatStrategyGroup.hidden = count === 1;
}

function normalizeCompact(value) {
  return value.replaceAll("-", "").replaceAll(":", "");
}

function buildPayload() {
  const data = new FormData(form);
  const start = normalizeCompact(String(data.get("dep_time") || ""));
  const end = unlimitedTime.checked ? "2400" : normalizeCompact(String(data.get("max_dep_time") || ""));
  const source = String(data.get("src_station") || "").trim();
  const destination = String(data.get("dst_station") || "").trim();

  if (source.length < 2 || destination.length < 2) throw new Error("출발역과 도착역을 입력해주세요.");
  if (!STATIONS[selectedOperator()].includes(source) || !STATIONS[selectedOperator()].includes(destination)) {
    throw new Error("목록에 있는 역을 선택해주세요.");
  }
  if (source === destination) throw new Error("출발역과 도착역은 달라야 합니다.");
  if (!depDate.value) throw new Error("출발 날짜를 선택해주세요.");
  if (!/^\d{4}$/.test(start) || !/^\d{4}$/.test(end)) throw new Error("검색 시간대를 선택해주세요.");
  if (end !== "2400" && end <= start) throw new Error("검색 종료 시각은 시작 시각보다 늦어야 합니다.");

  return {
    v: 1,
    action: "prepare_search",
    operator: selectedOperator(),
    dep_date: normalizeCompact(depDate.value),
    src_station: source,
    dst_station: destination,
    dep_time: start,
    max_dep_time: end,
    train_type: String(data.get("train_type") || "1"),
    seat_option: String(data.get("seat_option") || "1"),
    passenger_count: Number(data.get("passenger_count") || 1),
    seat_strategy: String(data.get("seat_strategy") || "1"),
  };
}

function buildStartParameter(data) {
  const stations = STATIONS[data.operator];
  const source = stations.indexOf(data.src_station).toString(36);
  const destination = stations.indexOf(data.dst_station).toString(36);
  const operator = data.operator === "srt" ? "s" : "k";
  const token = [
    "ma1_",
    operator,
    data.dep_date,
    source,
    destination,
    data.dep_time,
    data.max_dep_time,
    data.train_type,
    data.seat_option,
    String(data.passenger_count),
    data.seat_strategy,
  ].join("");
  if (token.length > 64) throw new Error("예약 조건을 채팅으로 전달할 수 없습니다.");
  return token;
}

function submit() {
  errorBox.textContent = "";
  try {
    const data = buildPayload();
    if (startTransport) {
      if (!/^[A-Za-z0-9_]{5,32}$/.test(botUsername)) {
        throw new Error("봇 연결 주소가 올바르지 않습니다. 채팅의 /start에서 다시 열어주세요.");
      }
      const link = `https://t.me/${botUsername}?start=${buildStartParameter(data)}`;
      tg?.HapticFeedback?.notificationOccurred("success");
      if (tg?.openTelegramLink) {
        tg.openTelegramLink(link);
      } else {
        location.assign(link);
      }
      return;
    }

    const payload = JSON.stringify(data);
    if (new TextEncoder().encode(payload).length > 4096) throw new Error("입력값이 너무 깁니다.");
    if (tg?.sendData) {
      tg.HapticFeedback?.notificationOccurred("success");
      tg.sendData(payload);
      return;
    }
    preview.hidden = false;
    preview.textContent = `브라우저 미리보기 — Telegram에서는 이 값이 봇으로 전송됩니다.\n\n${JSON.stringify(JSON.parse(payload), null, 2)}`;
  } catch (error) {
    errorBox.textContent = error instanceof Error ? error.message : "입력을 다시 확인해주세요.";
    tg?.HapticFeedback?.notificationOccurred("error");
    errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

configureOperators();
depDate.min = localDate(0);
depDate.max = localDate(365);
depDate.value = localDate(1);
renderStations();

operatorPicker.addEventListener("change", renderStations);
document.querySelector("#swap-stations").addEventListener("click", () => {
  [srcStation.value, dstStation.value] = [dstStation.value, srcStation.value];
});
unlimitedTime.addEventListener("change", () => {
  maxDepTime.disabled = unlimitedTime.checked;
  document.querySelector("#end-time-field").style.opacity = unlimitedTime.checked ? ".45" : "1";
});
document.querySelector("#passenger-minus").addEventListener("click", () => setPassengerCount(Number(passengerCount.value) - 1));
document.querySelector("#passenger-plus").addEventListener("click", () => setPassengerCount(Number(passengerCount.value) + 1));
form.addEventListener("submit", (event) => { event.preventDefault(); submit(); });

if (tg) {
  tg.ready();
  tg.expand();
  tg.disableVerticalSwipes?.();
  tg.MainButton.setText("이 조건으로 열차 보기");
  tg.MainButton.onClick(submit);
  tg.MainButton.show();
  submitButton.hidden = true;
}
