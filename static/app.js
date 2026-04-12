var socket = io();
var currentTab = "inbox";
var store = { inbox: [], outbox: [], sent: [] };

// ---------------------------------------------------------------------------
// SocketIO events
// ---------------------------------------------------------------------------

socket.on("inbox", function(data) {
  store.inbox = data || [];
  updateBadge("Inbox", store.inbox.length);
  document.getElementById("pollStatus").textContent = "🟢 Last sync: " + new Date().toLocaleTimeString();
  if (currentTab === "inbox") render();
});

socket.on("outbox_update", function(entry) {
  var idx = store.outbox.findIndex(function(m) { return m.id === entry.id; });
  if (idx >= 0) store.outbox[idx] = entry;
  else store.outbox.unshift(entry);
  var queued = store.outbox.filter(function(m) { return m.status === "queued"; }).length;
  updateBadge("Outbox", queued);
  if (currentTab === "outbox") render();
});

socket.on("sent_update", function(entry) {
  var idx = store.sent.findIndex(function(m) { return m.id === entry.id; });
  if (idx < 0) store.sent.unshift(entry);
  if (currentTab === "sent") render();
});

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function updateBadge(name, count) {
  var el = document.getElementById("badge" + name);
  if (count > 0) { el.textContent = count; el.style.display = ""; }
  else { el.style.display = "none"; }
}

function showTab(tab) {
  currentTab = tab;
  ["Inbox", "Outbox", "Sent"].forEach(function(name) {
    document.getElementById("tab" + name).classList.toggle("active", name.toLowerCase() === tab);
  });
  render();
}

function esc(s) {
  return (s || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function toast(msg) {
  var el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(function() { el.classList.remove("show"); }, 3000);
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function render() {
  var list = document.getElementById("list");
  var items = store[currentTab] || [];
  if (!items.length) {
    list.innerHTML = "<div class='item' style='color:#94a3b8;text-align:center;padding:24px;'>No messages</div>";
    return;
  }
  list.innerHTML = items.map(function(item) {
    if (currentTab === "inbox") {
      return "<div class='item'>"
        + "<div class='meta'><span><b>" + esc(item.number) + "</b></span><span>" + esc(item.timestamp || "") + "</span></div>"
        + "<div style='white-space:pre-wrap'>" + esc(item.message) + "</div>"
        + "<div style='margin-top:8px'><button class='btn danger' onclick='deleteSMS(" + item.modem_index + ")'>🗑 Delete</button></div>"
        + "</div>";
    }
    var cls = "status " + esc(item.status || "queued");
    var actions = "";
    if (item.status === "queued") {
      actions = "<button class='btn success' style='margin-top:8px;' onclick='sendOne(" + item.id + ")'>🚀 Send</button>";
    }
    return "<div class='item'>"
      + "<div class='meta'><span><b>" + esc(item.number) + "</b></span><span>" + esc(item.created_at || "") + "</span></div>"
      + "<div style='white-space:pre-wrap'>" + esc(item.message) + "</div>"
      + "<div style='margin-top:6px;display:flex;align-items:center;gap:8px;'>"
      + "<span class='" + cls + "'>" + esc(item.status || "queued") + "</span>"
      + (item.sent_at ? "<span style='font-size:11px;color:#64748b;'>sent " + esc(item.sent_at) + "</span>" : "")
      + "</div>"
      + actions
      + "</div>";
  }).join("");
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

function loadAll() {
  Promise.all([
    fetch("/inbox").then(function(r) { return r.json(); }),
    fetch("/outbox").then(function(r) { return r.json(); }),
    fetch("/sent").then(function(r) { return r.json(); })
  ]).then(function(values) {
    store.inbox  = values[0] || [];
    store.outbox = values[1] || [];
    store.sent   = values[2] || [];
    var queued = store.outbox.filter(function(m) { return m.status === "queued"; }).length;
    updateBadge("Inbox", store.inbox.length);
    updateBadge("Outbox", queued);
    document.getElementById("pollStatus").textContent = "🟢 Last sync: " + new Date().toLocaleTimeString();
    render();
  });
}

function queueSMS() {
  var numbers = document.getElementById("numbers").value.split(",");
  var message = document.getElementById("message").value.trim();
  if (!message) { toast("⚠️ Message is empty"); return; }
  fetch("/queue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ numbers: numbers, message: message })
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (data.status === "ok") {
      toast("📥 Added " + data.queued.length + " message(s) to outbox");
      document.getElementById("message").value = "";
      showTab("outbox");
    } else {
      toast("❌ Error: " + (data.error || "unknown"));
    }
  });
}

function sendOne(id) {
  fetch("/send/" + id, { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.entry && data.entry.status === "sent") toast("✅ Sent to " + data.entry.number);
      else toast("❌ Failed to send");
      loadAll();
    });
}

function sendAllQueued() {
  fetch("/send_all", { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      toast("✅ Sent " + data.sent_count + " message(s)");
      loadAll();
    });
}

function deleteSMS(index) {
  if (!confirm("Delete this SMS from modem?")) return;
  fetch("/delete/" + index)
    .then(function(r) { return r.json(); })
    .then(function() { loadAll(); });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadAll();
