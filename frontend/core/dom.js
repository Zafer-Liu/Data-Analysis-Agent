const SCROLL_THRESHOLD = 80;
let userScrolledUp = false;
let scrollLocked = false;

export function $(id) {
  return document.getElementById(id);
}

export function esc(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function messagesElement() {
  return $("messages");
}

function isNearBottom() {
  const messages = messagesElement();
  if (!messages) return true;
  return messages.scrollHeight - messages.scrollTop - messages.clientHeight <= SCROLL_THRESHOLD;
}

function onUserScroll() {
  if (scrollLocked) return;
  userScrolledUp = !isNearBottom();
}

document.addEventListener("DOMContentLoaded", () => {
  messagesElement()?.addEventListener("scroll", onUserScroll, { passive: true });
});

export function scrollBottom(force = false) {
  const messages = messagesElement();
  if (!messages || (!force && userScrolledUp)) return;

  scrollLocked = true;
  messages.scrollTop = messages.scrollHeight;
  requestAnimationFrame(() => {
    scrollLocked = false;
  });
}

export function scrollReset() {
  userScrolledUp = false;
  scrollBottom(true);
}

export function hideWelcome() {
  const welcome = $("welcome");
  if (welcome) welcome.classList.add("hidden");
}

export function showWelcome() {
  const welcome = $("welcome");
  if (welcome) welcome.classList.remove("hidden");
}

export const dom = Object.freeze({
  $,
  esc,
  scrollBottom,
  scrollReset,
  hideWelcome,
  showWelcome,
});
