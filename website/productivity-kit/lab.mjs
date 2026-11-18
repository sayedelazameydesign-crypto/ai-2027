const input = document.getElementById("lab-input");
const result = document.getElementById("lab-result");

function setResult(message, state) {
  result.textContent = message;
  result.dataset.state = state;
}

function checkFixture() {
  if (input.value === "ARENA-2027") {
    setResult("LAB_OK", "pass");
  } else {
    setResult("LAB_MISMATCH", "fail");
  }
}

document.getElementById("lab-run").addEventListener("click", checkFixture);
document.getElementById("lab-reset").addEventListener("click", () => {
  input.value = "";
  setResult("LAB_READY", "ready");
  input.focus();
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) checkFixture();
});

for (const name of ["neutral", "candidate"]) {
  const image = document.getElementById(`${name}-image`);
  const status = document.getElementById(`${name}-status`);
  const read = () => { status.textContent = image.naturalWidth > 0 ? "LOADED" : "NOT_LOADED"; };
  image.addEventListener("load", read);
  image.addEventListener("error", read);
  if (image.complete) read();
}
