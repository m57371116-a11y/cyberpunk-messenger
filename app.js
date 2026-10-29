/* ========== سایبر پانک — کلاینت پیشرفته ========== */

const API = ""; // همان origin وقتی با server.py باز شود

const AVATAR_COLORS = [
  '#e17076', '#7bc862', '#e5ca77', '#6ab3f3',
  '#ee7aae', '#faa774', '#a695e7', '#ff8e6b'
];

let currentUser = null;
let token = localStorage.getItem("cp_token") || "";
let users = [];
let chats = [];
let activeChatId = null;
let currentFilter = "all";
let createType = null;
let contextMsgId = null;
let contextChatId = null;
let editingMsgId = null;
let selectMode = false;
let selectedMsgIds = new Set();
let messagesCache = {};
let pollTimer = null;
let listPollTimer = null;
let heartbeatTimer = null;
let pendingFile = null; // { dataUrl, name, size, type, msgType }
let lastNotifiedMsgId = null;
let notifPermission = Notification.permission;

function getAvatarColor(name) {
  let h = 0;
  for (let i = 0; i < (name || "").length; i++) h = name.charCodeAt(i) + ((h << 5) - h);
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}
function formatTime(ts) {
  const t = ts > 1e12 ? ts : ts * 1000;
  return new Date(t).toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
}
function formatDate(ts) {
  const t = ts > 1e12 ? ts : ts * 1000;
  const d = new Date(t);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "امروز";
  if (d.toDateString() === yesterday.toDateString()) return "دیروز";
  return d.toLocaleDateString("fa-IR");
}
function formatSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}
function toast(msg, type = "info") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 3200);
}
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}
function tsMs(ts) {
  return ts > 1e12 ? ts : ts * 1000;
}

async function api(method, path, body) {
  const opts = {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {})
    }
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  let res;
  try {
    res = await fetch(API + path, opts);
  } catch (e) {
    toast("سرور در دسترس نیست. ابتدا server.py را اجرا کنید.", "error");
    throw e;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = data.error || "خطا";
    if (res.status === 401 && path !== "/api/login") logout(true);
    throw new Error(err);
  }
  return data;
}

/* ---------- Init & Auth ---------- */

async function init() {
  if (token) {
    try {
      const data = await api("GET", "/api/me");
      currentUser = data.user;
      showApp();
      return;
    } catch {
      token = "";
      localStorage.removeItem("cp_token");
    }
  }
  showAuth();
}

function showAuth() {
  document.getElementById("auth-screen").classList.add("active");
  document.getElementById("app-screen").classList.remove("active");
  stopPolling();
}

async function showApp() {
  document.getElementById("auth-screen").classList.remove("active");
  document.getElementById("app-screen").classList.add("active");
  renderSidebarUser();
  await Promise.all([refreshUsers(), refreshChats()]);
  startPolling();
  startHeartbeat();
  requestNotifPermission();
}

function startPolling() {
  stopPolling();
  listPollTimer = setInterval(() => { refreshChats().catch(() => {}); }, 4000);
  pollTimer = setInterval(() => {
    if (activeChatId) refreshMessages(activeChatId, true).catch(() => {});
  }, 2500);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  if (listPollTimer) clearInterval(listPollTimer);
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  pollTimer = listPollTimer = heartbeatTimer = null;
}

function startHeartbeat() {
  const beat = () => api("POST", "/api/heartbeat").catch(() => {});
  beat();
  heartbeatTimer = setInterval(beat, 20000);
}

function logout(silent) {
  token = "";
  localStorage.removeItem("cp_token");
  currentUser = null;
  activeChatId = null;
  messagesCache = {};
  chats = [];
  stopPolling();
  if (!silent) toast("خارج شدید");
  showAuth();
}

/* ---------- Notifications ---------- */

function requestNotifPermission() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    Notification.requestPermission().then(p => { notifPermission = p; });
  } else {
    notifPermission = Notification.permission;
  }
}

function playNotifSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.connect(g);
    g.connect(ctx.destination);
    o.frequency.value = 880;
    g.gain.value = 0.08;
    o.start();
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
    o.stop(ctx.currentTime + 0.3);
  } catch (_) {}
}

function showBrowserNotif(title, body, chatId) {
  if (notifPermission !== "granted") return;
  if (document.hidden === false && activeChatId === chatId) return;
  try {
    const n = new Notification(title, {
      body: body.slice(0, 120),
      icon: "/favicon.ico",
      tag: "cp-" + chatId,
    });
    n.onclick = () => {
      window.focus();
      openChat(chatId);
      n.close();
    };
    setTimeout(() => n.close(), 5000);
  } catch (_) {}
  playNotifSound();
}

/* ---------- Data ---------- */

async function refreshUsers() {
  const data = await api("GET", "/api/users");
  users = data.users || [];
  renderUsersList();
}

async function refreshChats() {
  const data = await api("GET", "/api/chats");
  const prev = chats;
  chats = data.chats || [];

  // نوتیفیکیشن برای پیام‌های جدید
  for (const c of chats) {
    if (c.unread > 0 && c.id !== activeChatId) {
      const prevChat = prev.find(x => x.id === c.id);
      if (!prevChat || c.unread > (prevChat.unread || 0)) {
        const title = getChatDisplayName(c);
        showBrowserNotif(title, c.preview || "پیام جدید", c.id);
      }
    }
  }

  renderChatList();
  if (activeChatId) {
    const still = chats.find(c => c.id === activeChatId);
    if (!still) {
      activeChatId = null;
      document.getElementById("chat-window").style.display = "none";
      document.getElementById("empty-state").style.display = "";
    }
  }
}

async function refreshMessages(chatId, silent) {
  const cached = messagesCache[chatId] || [];
  const since = cached.length ? cached[cached.length - 1].ts : 0;
  const data = await api("GET", `/api/chats/${chatId}/messages?since=${since}`);
  const incoming = data.messages || [];
  if (!incoming.length) {
    if (!silent) renderMessages(chats.find(c => c.id === chatId));
    return;
  }

  const existingIds = new Set(cached.map(m => m.id));
  const newOnes = incoming.filter(m => !existingIds.has(m.id));
  if (newOnes.length) {
    messagesCache[chatId] = [...cached, ...newOnes].sort((a, b) => a.ts - b.ts);
    // نوتیف اگر از دیگران باشد
    for (const m of newOnes) {
      if (m.sender_id !== currentUser.id && m.id !== lastNotifiedMsgId) {
        lastNotifiedMsgId = m.id;
        const chat = chats.find(c => c.id === chatId);
        const sender = users.find(u => u.id === m.sender_id);
        const title = sender ? sender.name : "پیام جدید";
        const body = m.msg_type === "image" ? "📷 تصویر" :
                     m.msg_type === "video" ? "🎬 ویدیو" :
                     m.msg_type === "file" ? "📎 فایل" : (m.text || "");
        showBrowserNotif(title, body, chatId);
      }
    }
  } else {
    // به‌روزرسانی ویرایش/حذف
    const map = new Map(incoming.map(m => [m.id, m]));
    messagesCache[chatId] = cached.map(m => map.get(m.id) || m);
  }

  if (activeChatId === chatId) {
    renderMessages(chats.find(c => c.id === chatId));
  }
}

/* ---------- Render ---------- */

function renderSidebarUser() {
  const el = document.getElementById("sidebar-user");
  if (!el || !currentUser) return;
  const color = getAvatarColor(currentUser.name);
  el.innerHTML = `
    <div class="avatar" style="background:${color}">${(currentUser.name || "?")[0]}</div>
    <div class="user-meta">
      <strong>${escapeHtml(currentUser.name)}</strong>
      <span>@${escapeHtml(currentUser.username)}</span>
    </div>
  `;
}

function getChatDisplayName(chat) {
  if (chat.type === "private") return chat.name || "چت خصوصی";
  return chat.name || "گروه";
}

function getOtherUserId(chat) {
  if (chat.type !== "private") return null;
  return (chat.members || []).find(id => id !== currentUser.id) || chat.other_id;
}

function renderChatList() {
  const list = document.getElementById("chat-list");
  if (!list) return;
  let filtered = chats;
  if (currentFilter === "private") filtered = chats.filter(c => c.type === "private");
  if (currentFilter === "group") filtered = chats.filter(c => c.type === "group");

  if (!filtered.length) {
    list.innerHTML = `<div class="empty-list">گفتگویی نیست</div>`;
    return;
  }

  list.innerHTML = filtered.map(chat => {
    const name = getChatDisplayName(chat);
    const color = getAvatarColor(name);
    const active = chat.id === activeChatId ? "active" : "";
    const onlineDot = chat.type === "private" && chat.other_online
      ? `<span class="online-dot" title="آنلاین"></span>` : "";
    const unread = chat.unread > 0
      ? `<span class="badge">${chat.unread > 99 ? "۹۹+" : chat.unread}</span>` : "";
    const typeIcon = chat.type === "group" ? `<i class="fas fa-users type-icon"></i>` : "";
    return `
      <div class="chat-item ${active}" data-id="${chat.id}" oncontextmenu="showChatContextMenu(event,'${chat.id}')">
        <div class="avatar-wrap">
          <div class="avatar" style="background:${color}">${name[0] || "?"}</div>
          ${onlineDot}
        </div>
        <div class="chat-item-body">
          <div class="chat-item-top">
            <strong>${typeIcon}${escapeHtml(name)}</strong>
            <span class="time">${formatTime(chat.last_message_at)}</span>
          </div>
          <div class="chat-item-bottom">
            <span class="preview">${escapeHtml(chat.preview || "")}</span>
            ${unread}
          </div>
        </div>
      </div>
    `;
  }).join("");

  list.querySelectorAll(".chat-item").forEach(el => {
    el.addEventListener("click", () => openChat(el.dataset.id));
  });
}

function renderUsersList(search) {
  const list = document.getElementById("users-list");
  if (!list) return;
  const q = (search || document.getElementById("user-search")?.value || "").trim().toLowerCase();
  let filtered = users.filter(u => u.id !== currentUser?.id);
  if (q) {
    filtered = filtered.filter(u =>
      (u.name || "").toLowerCase().includes(q) ||
      (u.username || "").toLowerCase().includes(q)
    );
  }
  if (!filtered.length) {
    list.innerHTML = `<div class="empty-list">کاربری یافت نشد</div>`;
    return;
  }
  list.innerHTML = filtered.map(u => {
    const color = getAvatarColor(u.name);
    const online = u.online ? `<span class="online-dot"></span>` : "";
    return `
      <div class="user-item" data-username="${escapeHtml(u.username)}">
        <div class="avatar-wrap">
          <div class="avatar" style="background:${color}">${(u.name || "?")[0]}</div>
          ${online}
        </div>
        <div class="user-meta">
          <strong>${escapeHtml(u.name)}</strong>
          <span>@${escapeHtml(u.username)} ${u.online ? "· آنلاین" : ""}</span>
        </div>
      </div>
    `;
  }).join("");

  list.querySelectorAll(".user-item").forEach(el => {
    el.addEventListener("click", () => startPrivateChatWith(el.dataset.username));
  });
}

async function startPrivateChatWith(username) {
  try {
    const data = await api("POST", "/api/chats", { type: "private", username });
    await refreshChats();
    openChat(data.chat.id);
    // بستن پنل کاربران اگر باز باشد
    document.getElementById("users-panel")?.classList.remove("open");
  } catch (e) {
    toast(e.message, "error");
  }
}

async function openChat(chatId) {
  activeChatId = chatId;
  selectMode = false;
  selectedMsgIds.clear();
  document.getElementById("selection-bar").style.display = "none";
  document.getElementById("empty-state").style.display = "none";
  document.getElementById("chat-window").style.display = "flex";
  clearFilePreview();

  const chat = chats.find(c => c.id === chatId);
  if (!chat) return;

  // هدر
  const name = getChatDisplayName(chat);
  document.getElementById("chat-title").textContent = name;
  document.getElementById("chat-avatar").textContent = name[0] || "?";
  document.getElementById("chat-avatar").style.background = getAvatarColor(name);

  if (chat.type === "private") {
    document.getElementById("chat-subtitle").textContent = chat.other_online ? "آنلاین" : "آفلاین";
    document.getElementById("chat-subtitle").className = chat.other_online ? "online-text" : "";
  } else {
    const count = (chat.members || []).length;
    document.getElementById("chat-subtitle").textContent = `${count} عضو`;
    document.getElementById("chat-subtitle").className = "";
  }

  renderChatList();

  // پیام‌ها
  if (!messagesCache[chatId]) {
    messagesCache[chatId] = [];
    await refreshMessages(chatId, false);
  } else {
    renderMessages(chat);
    refreshMessages(chatId, true).catch(() => {});
  }

  // موبایل
  document.querySelector(".sidebar")?.classList.add("hidden-mobile");
  document.querySelector(".chat-area")?.classList.add("visible-mobile");
}

function fileUrl(msg) {
  if (msg.file_url) return msg.file_url;
  if (msg.msg_type && msg.msg_type !== "text" && msg.file_name) {
    // سرور فایل را با msg_id_filename ذخیره می‌کند
    return `/uploads/${msg.id}_${msg.file_name.replace(/[^\w\.\-\u0600-\u06FF]/g, "_")}`;
  }
  return null;
}

function renderMessages(chat) {
  const box = document.getElementById("messages");
  if (!box || !chat) return;
  const msgs = messagesCache[chat.id] || [];
  let lastDate = "";
  let html = "";

  for (const m of msgs) {
    const d = formatDate(m.ts);
    if (d !== lastDate) {
      html += `<div class="date-sep"><span>${d}</span></div>`;
      lastDate = d;
    }
    const mine = m.sender_id === currentUser.id;
    const sender = users.find(u => u.id === m.sender_id);
    const senderName = sender ? sender.name : "کاربر";
    const deleted = m.deleted_for_all;
    const selected = selectedMsgIds.has(m.id) ? "selected" : "";
    const mt = m.msg_type || "text";

    let body = "";
    if (deleted) {
      body = `<div class="msg-deleted"><i class="fas fa-ban"></i> این پیام حذف شده</div>`;
    } else if (mt === "image") {
      const url = fileUrl(m);
      body = `
        <div class="msg-media">
          <img src="${url}" alt="تصویر" loading="lazy" onclick="window.open('${url}','_blank')">
          ${m.text && m.text !== m.file_name ? `<div class="msg-caption">${escapeHtml(m.text)}</div>` : ""}
        </div>`;
    } else if (mt === "video") {
      const url = fileUrl(m);
      body = `
        <div class="msg-media">
          <video src="${url}" controls preload="metadata"></video>
          ${m.text && m.text !== m.file_name ? `<div class="msg-caption">${escapeHtml(m.text)}</div>` : ""}
        </div>`;
    } else if (mt === "file") {
      const url = fileUrl(m);
      body = `
        <a class="msg-file" href="${url}" download="${escapeHtml(m.file_name || "file")}" target="_blank">
          <i class="fas fa-file-alt"></i>
          <div>
            <strong>${escapeHtml(m.file_name || "فایل")}</strong>
            <span>${formatSize(m.file_size)}</span>
          </div>
        </a>
        ${m.text && m.text !== m.file_name ? `<div class="msg-caption">${escapeHtml(m.text)}</div>` : ""}`;
    } else {
      body = `<div class="msg-text">${escapeHtml(m.text)}</div>`;
    }

    html += `
      <div class="msg ${mine ? "mine" : "other"} ${selected}" data-id="${m.id}"
           oncontextmenu="showMsgContextMenu(event,'${m.id}')"
           onclick="if(selectMode) toggleMsgSelection('${m.id}')">
        ${!mine && chat.type === "group" ? `<div class="msg-sender">${escapeHtml(senderName)}</div>` : ""}
        ${body}
        <div class="msg-meta">
          ${m.edited ? '<span class="edited">ویرایش‌شده</span>' : ""}
          <span>${formatTime(m.ts)}</span>
        </div>
      </div>`;
  }

  box.innerHTML = html || `<div class="empty-msgs">هنوز پیامی نیست. اولین پیام را بفرستید!</div>`;
  box.scrollTop = box.scrollHeight;
}

/* ---------- File attach ---------- */

function clearFilePreview() {
  pendingFile = null;
  const fp = document.getElementById("file-preview");
  if (fp) fp.style.display = "none";
  document.getElementById("preview-img").style.display = "none";
  document.getElementById("preview-video").style.display = "none";
  document.getElementById("preview-file-info").style.display = "none";
  document.getElementById("file-input").value = "";
}

function handleFileSelect(file) {
  if (!file) return;
  if (file.size > 15 * 1024 * 1024) {
    toast("حجم فایل بیشتر از ۱۵ مگابایت است", "error");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    let msgType = "file";
    if (file.type.startsWith("image/")) msgType = "image";
    else if (file.type.startsWith("video/")) msgType = "video";

    pendingFile = {
      dataUrl: reader.result,
      name: file.name,
      size: file.size,
      type: file.type,
      msgType
    };

    const fp = document.getElementById("file-preview");
    fp.style.display = "block";
    document.getElementById("preview-img").style.display = "none";
    document.getElementById("preview-video").style.display = "none";
    document.getElementById("preview-file-info").style.display = "none";

    if (msgType === "image") {
      const img = document.getElementById("preview-img");
      img.src = reader.result;
      img.style.display = "block";
    } else if (msgType === "video") {
      const v = document.getElementById("preview-video");
      v.src = reader.result;
      v.style.display = "block";
    } else {
      document.getElementById("preview-file-info").style.display = "flex";
      document.getElementById("preview-file-name").textContent = file.name;
      document.getElementById("preview-file-size").textContent = formatSize(file.size);
    }
  };
  reader.readAsDataURL(file);
}

/* ---------- Send ---------- */

async function sendMessage() {
  if (!activeChatId) return;
  const input = document.getElementById("message-input");
  const text = (input.value || "").trim();

  if (pendingFile) {
    try {
      const body = {
        text: text || pendingFile.name,
        msg_type: pendingFile.msgType,
        file_data: pendingFile.dataUrl,
        file_name: pendingFile.name,
        mime_type: pendingFile.type,
      };
      const data = await api("POST", `/api/chats/${activeChatId}/messages`, body);
      if (!messagesCache[activeChatId]) messagesCache[activeChatId] = [];
      messagesCache[activeChatId].push(data.message);
      input.value = "";
      input.style.height = "auto";
      clearFilePreview();
      renderMessages(chats.find(c => c.id === activeChatId));
      refreshChats().catch(() => {});
    } catch (e) {
      toast(e.message, "error");
    }
    return;
  }

  if (!text) return;
  if (editingMsgId) {
    try {
      const data = await api("PUT", `/api/messages/${editingMsgId}`, { text });
      const list = messagesCache[activeChatId] || [];
      const idx = list.findIndex(m => m.id === editingMsgId);
      if (idx >= 0) list[idx] = data.message;
      editingMsgId = null;
      input.value = "";
      input.style.height = "auto";
      renderMessages(chats.find(c => c.id === activeChatId));
      toast("پیام ویرایش شد", "success");
    } catch (e) {
      toast(e.message, "error");
    }
    return;
  }

  try {
    const data = await api("POST", `/api/chats/${activeChatId}/messages`, { text, msg_type: "text" });
    if (!messagesCache[activeChatId]) messagesCache[activeChatId] = [];
    messagesCache[activeChatId].push(data.message);
    input.value = "";
    input.style.height = "auto";
    renderMessages(chats.find(c => c.id === activeChatId));
    refreshChats().catch(() => {});
  } catch (e) {
    toast(e.message, "error");
  }
}

/* ---------- Select / Delete ---------- */

function enterSelectMode(firstMsgId) {
  selectMode = true;
  selectedMsgIds.clear();
  if (firstMsgId) selectedMsgIds.add(firstMsgId);
  document.getElementById("selection-bar").style.display = "flex";
  updateSelectionUI();
  renderMessages(chats.find(c => c.id === activeChatId));
}

function exitSelectMode() {
  selectMode = false;
  selectedMsgIds.clear();
  document.getElementById("selection-bar").style.display = "none";
  renderMessages(chats.find(c => c.id === activeChatId));
}

function toggleMsgSelection(msgId) {
  if (selectedMsgIds.has(msgId)) selectedMsgIds.delete(msgId);
  else selectedMsgIds.add(msgId);
  updateSelectionUI();
  document.querySelector(`.msg[data-id="${msgId}"]`)?.classList.toggle("selected");
}

function updateSelectionUI() {
  const n = selectedMsgIds.size;
  document.getElementById("selection-count").textContent = n + " انتخاب‌شده";
}

async function bulkDelete(forAll) {
  if (!selectedMsgIds.size) return;
  try {
    await api("POST", "/api/messages/bulk-delete", {
      ids: [...selectedMsgIds],
      mode: forAll ? "all" : "me"
    });
    const list = messagesCache[activeChatId] || [];
    if (forAll) {
      selectedMsgIds.forEach(id => {
        const m = list.find(x => x.id === id);
        if (m) { m.deleted_for_all = 1; m.text = ""; }
      });
    } else {
      messagesCache[activeChatId] = list.filter(m => !selectedMsgIds.has(m.id));
    }
    exitSelectMode();
    toast("حذف شد", "success");
  } catch (e) {
    toast(e.message, "error");
  }
}

/* ---------- Context menus ---------- */

function showMsgContextMenu(e, msgId) {
  e.preventDefault();
  e.stopPropagation();
  if (selectMode) return;
  contextMsgId = msgId;
  const menu = document.getElementById("msg-context-menu");
  menu.style.display = "block";
  menu.style.left = Math.min(e.clientX, window.innerWidth - 180) + "px";
  menu.style.top = Math.min(e.clientY, window.innerHeight - 140) + "px";
}

function showChatContextMenu(e, chatId) {
  e.preventDefault();
  e.stopPropagation();
  contextChatId = chatId;
  const menu = document.getElementById("chat-context-menu");
  menu.style.display = "block";
  menu.style.left = Math.min(e.clientX, window.innerWidth - 160) + "px";
  menu.style.top = Math.min(e.clientY, window.innerHeight - 80) + "px";
}

function hideAllContextMenus() {
  document.getElementById("msg-context-menu").style.display = "none";
  document.getElementById("chat-context-menu").style.display = "none";
}

async function deleteChatForMe(chatId) {
  try {
    await api("DELETE", `/api/chats/${chatId}`);
    chats = chats.filter(c => c.id !== chatId);
    if (activeChatId === chatId) {
      activeChatId = null;
      document.getElementById("chat-window").style.display = "none";
      document.getElementById("empty-state").style.display = "";
    }
    renderChatList();
    toast("گفتگو حذف شد");
  } catch (e) {
    toast(e.message, "error");
  }
}

/* ---------- Modals ---------- */

function openCreateModal(type) {
  createType = type;
  const title = type === "group" ? "ساخت گروه" : "چت خصوصی";
  document.getElementById("create-modal-title").textContent = title;
  document.getElementById("create-name-label").textContent = type === "group" ? "نام گروه" : "نام کاربری طرف";
  document.getElementById("create-desc-group").style.display = type === "group" ? "" : "none";
  document.getElementById("create-members-group").style.display = type === "group" ? "" : "none";
  document.getElementById("create-name").value = "";
  document.getElementById("create-desc").value = "";
  document.getElementById("create-members").value = "";
  document.getElementById("modal-create").classList.add("open");
}

async function openChatInfo() {
  if (!activeChatId) return;
  try {
    const data = await api("GET", `/api/chats/${activeChatId}/info`);
    const chat = data.chat;
    const body = document.getElementById("info-body");
    const isGroup = chat.type === "group";
    const isAdmin = chat.my_role === "admin";

    let membersHtml = (chat.members_detail || []).map(m => {
      const color = getAvatarColor(m.name);
      const online = m.online ? `<span class="online-dot"></span>` : "";
      const roleBadge = m.role === "admin" ? `<span class="role-badge">ادمین</span>` : "";
      let actions = "";
      if (isGroup && isAdmin && m.id !== currentUser.id) {
        actions = `<button class="btn-text danger small" onclick="removeMember('${m.id}')">حذف</button>`;
      }
      if (isGroup && m.id === currentUser.id) {
        actions = `<button class="btn-text danger small" onclick="removeMember('${m.id}')">خروج از گروه</button>`;
      }
      return `
        <div class="member-row">
          <div class="avatar-wrap">
            <div class="avatar small" style="background:${color}">${(m.name || "?")[0]}</div>
            ${online}
          </div>
          <div class="member-info">
            <strong>${escapeHtml(m.name)} ${roleBadge}</strong>
            <span>@${escapeHtml(m.username)} ${m.online ? "· آنلاین" : ""}</span>
          </div>
          ${actions}
        </div>`;
    }).join("");

    body.innerHTML = `
      <div class="info-header">
        <div class="avatar large" style="background:${getAvatarColor(chat.name)}">${(chat.name || "?")[0]}</div>
        <h3>${escapeHtml(chat.name)}</h3>
        <p>${isGroup ? (chat.description || "بدون توضیحات") : ""}</p>
        <p class="muted">${isGroup ? (chat.members_detail || []).length + " عضو" : "چت خصوصی"}</p>
      </div>
      ${isGroup && isAdmin ? `
        <div class="info-section">
          <h4>ویرایش گروه</h4>
          <div class="input-group">
            <input type="text" id="edit-group-name" value="${escapeHtml(chat.name)}" placeholder="نام گروه">
          </div>
          <div class="input-group">
            <textarea id="edit-group-desc" rows="2" placeholder="توضیحات">${escapeHtml(chat.description || "")}</textarea>
          </div>
          <button class="btn-primary small" id="btn-save-group">ذخیره</button>
        </div>
        <div class="info-section">
          <h4>افزودن عضو</h4>
          <div class="input-group row">
            <input type="text" id="add-member-user" placeholder="نام کاربری">
            <button class="btn-primary small" id="btn-add-member">افزودن</button>
          </div>
        </div>
      ` : ""}
      <div class="info-section">
        <h4>اعضا</h4>
        <div class="members-list">${membersHtml}</div>
      </div>
    `;

    document.getElementById("modal-info").classList.add("open");

    if (isGroup && isAdmin) {
      document.getElementById("btn-save-group")?.addEventListener("click", async () => {
        try {
          await api("PUT", `/api/chats/${activeChatId}`, {
            name: document.getElementById("edit-group-name").value,
            description: document.getElementById("edit-group-desc").value,
          });
          toast("گروه به‌روز شد", "success");
          await refreshChats();
          openChat(activeChatId);
          document.getElementById("modal-info").classList.remove("open");
        } catch (e) {
          toast(e.message, "error");
        }
      });
      document.getElementById("btn-add-member")?.addEventListener("click", async () => {
        const un = document.getElementById("add-member-user").value.trim();
        if (!un) return;
        try {
          await api("POST", `/api/chats/${activeChatId}/members`, { username: un });
          toast("عضو اضافه شد", "success");
          openChatInfo();
          refreshChats();
        } catch (e) {
          toast(e.message, "error");
        }
      });
    }
  } catch (e) {
    toast(e.message, "error");
  }
}

async function removeMember(userId) {
  if (!activeChatId) return;
  const isSelf = userId === currentUser.id;
  if (!confirm(isSelf ? "از گروه خارج می‌شوید؟" : "این عضو حذف شود؟")) return;
  try {
    await api("DELETE", `/api/chats/${activeChatId}/members/${userId}`);
    toast(isSelf ? "از گروه خارج شدید" : "عضو حذف شد", "success");
    document.getElementById("modal-info").classList.remove("open");
    if (isSelf) {
      activeChatId = null;
      document.getElementById("chat-window").style.display = "none";
      document.getElementById("empty-state").style.display = "";
    }
    await refreshChats();
  } catch (e) {
    toast(e.message, "error");
  }
}

function openUserProfile(userId) {
  const u = users.find(x => x.id === userId);
  if (!u) return;
  const body = document.getElementById("user-profile-body");
  const color = getAvatarColor(u.name);
  body.innerHTML = `
    <div class="info-header">
      <div class="avatar large" style="background:${color}">${(u.name || "?")[0]}</div>
      <h3>${escapeHtml(u.name)}</h3>
      <p>@${escapeHtml(u.username)}</p>
      <p class="${u.online ? "online-text" : "muted"}">${u.online ? "آنلاین" : "آفلاین"}</p>
      <p>${escapeHtml(u.bio || "بدون بیو")}</p>
      <button class="btn-primary" onclick="startPrivateChatWith('${escapeHtml(u.username)}');document.getElementById('modal-user-profile').classList.remove('open')">ارسال پیام</button>
    </div>
  `;
  document.getElementById("modal-user-profile").classList.add("open");
}

/* ---------- Event bindings ---------- */

document.addEventListener("DOMContentLoaded", () => {
  // Auth tabs
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".auth-form").forEach(f => f.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(btn.dataset.tab === "login" ? "login-form" : "register-form").classList.add("active");
    });
  });

  document.getElementById("login-form")?.addEventListener("submit", async e => {
    e.preventDefault();
    try {
      const data = await api("POST", "/api/login", {
        username: document.getElementById("login-username").value,
        password: document.getElementById("login-password").value,
      });
      token = data.token;
      localStorage.setItem("cp_token", token);
      currentUser = data.user;
      showApp();
      toast("خوش آمدید!", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("register-form")?.addEventListener("submit", async e => {
    e.preventDefault();
    try {
      const data = await api("POST", "/api/register", {
        username: document.getElementById("reg-username").value,
        name: document.getElementById("reg-name").value,
        password: document.getElementById("reg-password").value,
      });
      token = data.token;
      localStorage.setItem("cp_token", token);
      currentUser = data.user;
      showApp();
      toast("ثبت‌نام موفق!", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("btn-logout")?.addEventListener("click", () => logout(false));
  document.getElementById("btn-profile")?.addEventListener("click", () => {
    document.getElementById("profile-name").value = currentUser.name || "";
    document.getElementById("profile-username").value = currentUser.username || "";
    document.getElementById("profile-bio").value = currentUser.bio || "";
    document.getElementById("profile-avatar").textContent = (currentUser.name || "?")[0];
    document.getElementById("profile-avatar").style.background = getAvatarColor(currentUser.name);
    document.getElementById("modal-profile").classList.add("open");
  });

  document.getElementById("btn-save-profile")?.addEventListener("click", async () => {
    try {
      const data = await api("PUT", "/api/profile", {
        name: document.getElementById("profile-name").value,
        bio: document.getElementById("profile-bio").value,
      });
      currentUser = data.user;
      renderSidebarUser();
      document.getElementById("modal-profile").classList.remove("open");
      toast("پروفایل ذخیره شد", "success");
    } catch (e) {
      toast(e.message, "error");
    }
  });

  document.getElementById("btn-new")?.addEventListener("click", () => {
    // منوی کوچک: گروه یا خصوصی
    const choice = confirm("گروه جدید بسازید؟\nOK = گروه | Cancel = چت خصوصی");
    openCreateModal(choice ? "group" : "private");
  });

  document.getElementById("btn-create-confirm")?.addEventListener("click", async () => {
    try {
      const name = document.getElementById("create-name").value.trim();
      const description = document.getElementById("create-desc").value.trim();
      const membersRaw = document.getElementById("create-members").value;
      const members = membersRaw.split(",").map(s => s.trim()).filter(Boolean);
      const payload = createType === "group"
        ? { type: "group", name, description, members }
        : { type: "private", username: name };
      const data = await api("POST", "/api/chats", payload);
      document.getElementById("modal-create").classList.remove("open");
      await refreshChats();
      openChat(data.chat.id);
      toast("ایجاد شد", "success");
    } catch (e) {
      toast(e.message, "error");
    }
  });

  document.getElementById("btn-send")?.addEventListener("click", sendMessage);
  document.getElementById("message-input")?.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  document.getElementById("message-input")?.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 120) + "px";
  });

  document.getElementById("btn-attach")?.addEventListener("click", () => {
    document.getElementById("file-input").click();
  });
  document.getElementById("file-input")?.addEventListener("change", e => {
    const f = e.target.files?.[0];
    if (f) handleFileSelect(f);
  });
  document.getElementById("btn-cancel-file")?.addEventListener("click", clearFilePreview);

  document.getElementById("btn-select-mode")?.addEventListener("click", () => enterSelectMode());
  document.getElementById("btn-cancel-select")?.addEventListener("click", exitSelectMode);
  document.getElementById("btn-bulk-delete-me")?.addEventListener("click", () => bulkDelete(false));
  document.getElementById("btn-bulk-delete-all")?.addEventListener("click", () => bulkDelete(true));
  document.getElementById("btn-chat-info")?.addEventListener("click", openChatInfo);

  document.getElementById("btn-back")?.addEventListener("click", () => {
    document.querySelector(".sidebar")?.classList.remove("hidden-mobile");
    document.querySelector(".chat-area")?.classList.remove("visible-mobile");
  });

  // فیلترها
  document.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentFilter = btn.dataset.filter;
      renderChatList();
    });
  });

  document.getElementById("user-search")?.addEventListener("input", e => renderUsersList(e.target.value));

  // بستن مودال‌ها
  document.querySelectorAll(".close-modal").forEach(btn => {
    btn.addEventListener("click", () => btn.closest(".modal").classList.remove("open"));
  });
  document.querySelectorAll(".modal").forEach(m => {
    m.addEventListener("click", e => { if (e.target === m) m.classList.remove("open"); });
  });

  // منوهای راست‌کلیک
  document.getElementById("msg-context-menu")?.addEventListener("click", async e => {
    const btn = e.target.closest("button");
    if (!btn || !contextMsgId) return;
    const action = btn.dataset.action;
    hideAllContextMenus();
    if (action === "edit") {
      const msg = (messagesCache[activeChatId] || []).find(m => m.id === contextMsgId);
      if (!msg || msg.sender_id !== currentUser.id) {
        toast("فقط پیام خودتان", "error");
        return;
      }
      if ((msg.msg_type || "text") !== "text") {
        toast("فقط پیام متنی", "error");
        return;
      }
      editingMsgId = contextMsgId;
      document.getElementById("message-input").value = msg.text;
      document.getElementById("message-input").focus();
      toast("در حال ویرایش — Enter برای ذخیره");
    } else if (action === "delete-me") {
      try {
        await api("DELETE", `/api/messages/${contextMsgId}`, { mode: "me" });
        messagesCache[activeChatId] = (messagesCache[activeChatId] || []).filter(m => m.id !== contextMsgId);
        renderMessages(chats.find(c => c.id === activeChatId));
      } catch (err) { toast(err.message, "error"); }
    } else if (action === "delete-all") {
      try {
        await api("DELETE", `/api/messages/${contextMsgId}`, { mode: "all" });
        const m = (messagesCache[activeChatId] || []).find(x => x.id === contextMsgId);
        if (m) { m.deleted_for_all = 1; m.text = ""; }
        renderMessages(chats.find(c => c.id === activeChatId));
      } catch (err) { toast(err.message, "error"); }
    }
  });

  document.getElementById("chat-context-menu")?.addEventListener("click", e => {
    const btn = e.target.closest("button");
    if (!btn || !contextChatId) return;
    hideAllContextMenus();
    if (btn.dataset.action === "delete-chat") deleteChatForMe(contextChatId);
  });

  document.addEventListener("click", hideAllContextMenus);
  document.addEventListener("contextmenu", e => {
    if (!e.target.closest(".msg") && !e.target.closest(".chat-item")) hideAllContextMenus();
  });

  // Drag & drop فایل
  const chatArea = document.querySelector(".chat-area");
  if (chatArea) {
    chatArea.addEventListener("dragover", e => { e.preventDefault(); chatArea.classList.add("drag-over"); });
    chatArea.addEventListener("dragleave", () => chatArea.classList.remove("drag-over"));
    chatArea.addEventListener("drop", e => {
      e.preventDefault();
      chatArea.classList.remove("drag-over");
      const f = e.dataTransfer.files?.[0];
      if (f && activeChatId) handleFileSelect(f);
    });
  }

  init();
});
