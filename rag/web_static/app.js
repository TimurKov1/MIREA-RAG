const form = document.querySelector("#chat-form");
const question = document.querySelector("#question");
const messages = document.querySelector("#messages");
const status = document.querySelector("#status");
const send = document.querySelector("#send");
const reset = document.querySelector("#reset");
const introduction = messages.firstElementChild;
const endpoint = "./api/chat";

function setBusy(busy) {
  send.disabled = busy;
  reset.disabled = busy;
  question.readOnly = busy;
}

function addMessage(text, author, sources = []) {
  const article = document.createElement("article");
  article.className = author;
  const label = document.createElement("h2");
  label.textContent = author === "user" ? "Вы" : "Помощник";
  const content = document.createElement("p");
  content.textContent = text;
  article.append(label, content);

  if (sources.length) {
    const heading = document.createElement("h3");
    heading.textContent = "Источники";
    const list = document.createElement("ol");
    for (const source of sources) {
      const item = document.createElement("li");
      if (source.url && /^https?:\/\//i.test(source.url)) {
        const link = document.createElement("a");
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = source.title;
        item.append(link);
      } else {
        item.textContent = source.title;
      }
      list.append(item);
    }
    article.append(heading, list);
  }

  introduction.remove();
  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

async function requireSuccess(response) {
  if (response.ok) return;
  const error = await response.json().catch(() => ({}));
  const fallback = response.status === 422
    ? "Введите вопрос длиной до 500 символов."
    : "Сервис временно недоступен. Попробуйте ещё раз.";
  throw new Error(typeof error.detail === "string" ? error.detail : fallback);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = question.value.trim();
  if (!text || send.disabled) return;
  setBusy(true);
  status.textContent = "Помощник готовит ответ…";
  const pending = addMessage(text, "user");
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: text}),
    });
    await requireSuccess(response);
    const data = await response.json();
    addMessage(data.answer, "assistant", data.sources);
    question.value = "";
    status.textContent = "";
  } catch (error) {
    pending.remove();
    if (!messages.childElementCount) messages.append(introduction);
    status.textContent = error.message;
  } finally {
    setBusy(false);
    question.focus();
  }
});

reset.addEventListener("click", async () => {
  setBusy(true);
  try {
    await requireSuccess(await fetch(endpoint, {method: "DELETE"}));
    messages.replaceChildren(introduction);
    question.value = "";
    status.textContent = "Диалог очищен.";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    setBusy(false);
    question.focus();
  }
});
