const SCROLL_THRESHOLD = 80;
let userScrolledUp = false;
let scrollLocked = false;
let scrollBtn = null;

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

function updateScrollBtn() {
  if (!scrollBtn) return;
  scrollBtn.classList.toggle("visible", userScrolledUp);
}

function onUserScroll() {
  if (scrollLocked) return;
  userScrolledUp = !isNearBottom();
  updateScrollBtn();
}

function onUserWheel(event) {
  if (event.deltaY < 0) {
    userScrolledUp = true;
    updateScrollBtn();
    return;
  }
  requestAnimationFrame(() => {
    userScrolledUp = !isNearBottom();
    updateScrollBtn();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const messages = messagesElement();
  messages?.addEventListener("scroll", onUserScroll, { passive: true });
  messages?.addEventListener("wheel", onUserWheel, { passive: true });

  // Floating scroll-to-bottom button
  const chatArea = document.querySelector(".chat-area");
  if (chatArea) {
    scrollBtn = document.createElement("button");
    scrollBtn.className = "scroll-to-bottom-btn";
    scrollBtn.type = "button";
    scrollBtn.setAttribute("aria-label", "Scroll to bottom");
    scrollBtn.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"' +
      ' stroke="currentColor" stroke-width="2" stroke-linecap="round"' +
      ' stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    scrollBtn.addEventListener("click", () => scrollReset());
    chatArea.appendChild(scrollBtn);
  }
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
  updateScrollBtn();
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
