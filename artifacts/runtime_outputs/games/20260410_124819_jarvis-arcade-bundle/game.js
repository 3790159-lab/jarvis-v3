const scoreEl = document.getElementById("score");
const timeEl = document.getElementById("time");
const targetEl = document.getElementById("target");
const messageEl = document.getElementById("message");
const clickBtn = document.getElementById("clickBtn");
const resetBtn = document.getElementById("resetBtn");

const initialState = { score: 0, time: 20, target: 15, finished: false };
let state = { ...initialState };
let timer = null;

function render() {
  scoreEl.textContent = String(state.score);
  timeEl.textContent = String(state.time);
  targetEl.textContent = String(state.target);
}

function finish(message) {
  state.finished = true;
  clickBtn.disabled = true;
  messageEl.textContent = message;
}

function startTimer() {
  if (timer) clearInterval(timer);
  timer = setInterval(() => {
    if (state.finished) {
      clearInterval(timer);
      return;
    }
    state.time -= 1;
    render();
    if (state.time <= 0) {
      clearInterval(timer);
      if (state.score >= state.target) {
        finish("You win!");
      } else {
        finish("Time is over. Try again.");
      }
    }
  }, 1000);
}

clickBtn.addEventListener("click", () => {
  if (state.finished) return;
  state.score += 1;
  render();
  if (state.score >= state.target) {
    clearInterval(timer);
    finish("Target reached. You win!");
  }
});

resetBtn.addEventListener("click", () => {
  state = { ...initialState };
  clickBtn.disabled = false;
  messageEl.textContent = "";
  render();
  startTimer();
});

render();
startTimer();
