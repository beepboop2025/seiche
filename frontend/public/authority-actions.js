(function () {
  "use strict";

  function canonical(root) {
    var explicit = root.getAttribute("data-url");
    var link = document.querySelector('link[rel="canonical"]');
    return explicit || (link && link.href) || (location.origin + location.pathname);
  }

  function campaignUrl(url, product, action) {
    var next = new URL(url, location.href);
    next.searchParams.set("utm_source", product.toLowerCase().replace(/[^a-z0-9]+/g, "_"));
    next.searchParams.set("utm_medium", "earned_share");
    next.searchParams.set("utm_campaign", "citable_research");
    next.searchParams.set("utm_content", action);
    return next.toString();
  }

  function copy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return false; });
    }
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
    area.remove();
    return Promise.resolve(ok);
  }

  function wrap(ctx, text, x, y, width, lineHeight, maxLines) {
    var words = String(text || "").trim().split(/\s+/);
    var line = "";
    var lines = 0;
    words.forEach(function (word, index) {
      if (lines >= maxLines) return;
      var probe = line ? line + " " + word : word;
      if (ctx.measureText(probe).width > width && line) {
        ctx.fillText(line, x, y);
        y += lineHeight;
        lines += 1;
        line = word;
      } else {
        line = probe;
      }
      if (index === words.length - 1 && line && lines < maxLines) ctx.fillText(line, x, y);
    });
  }

  function card(root) {
    var canvas = document.createElement("canvas");
    canvas.width = 1200;
    canvas.height = 630;
    var ctx = canvas.getContext("2d");
    var product = root.getAttribute("data-product") || "Research";
    var title = root.getAttribute("data-title") || document.title;
    var description = root.getAttribute("data-description") ||
      (document.querySelector('meta[name="description"]') || {}).content || "";
    var url = canonical(root).replace(/^https?:\/\//, "");
    var gradient = ctx.createLinearGradient(0, 0, 1200, 630);
    gradient.addColorStop(0, "#07070c");
    gradient.addColorStop(1, "#17132b");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 1200, 630);
    ctx.strokeStyle = "#393356";
    ctx.strokeRect(28.5, 28.5, 1143, 573);
    ctx.fillStyle = "#b9adff";
    ctx.font = "600 23px ui-monospace, monospace";
    ctx.fillText(product.toUpperCase(), 72, 92);
    ctx.fillStyle = "#f1f1f6";
    ctx.font = "700 54px system-ui, sans-serif";
    wrap(ctx, title, 72, 202, 1040, 66, 3);
    ctx.fillStyle = "#b8bbca";
    ctx.font = "27px system-ui, sans-serif";
    wrap(ctx, description, 72, 416, 1010, 38, 3);
    ctx.strokeStyle = "#393356";
    ctx.beginPath(); ctx.moveTo(72, 520.5); ctx.lineTo(1128, 520.5); ctx.stroke();
    ctx.fillStyle = "#b9adff";
    ctx.font = "21px ui-monospace, monospace";
    ctx.fillText(url, 72, 563);
    return canvas;
  }

  function downloadCard(root) {
    var canvas = card(root);
    var product = (root.getAttribute("data-product") || "research").toLowerCase().replace(/[^a-z0-9]+/g, "-");
    var a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = product + "-citation-card.png";
    a.click();
  }

  function emit(root, action) {
    var endpoint = root.getAttribute("data-event-endpoint");
    var surface = root.getAttribute("data-event-surface");
    if (!endpoint || !surface) return;
    fetch(endpoint, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({surface: surface, event: action}),
      keepalive: true
    }).catch(function () {});
  }

  document.querySelectorAll("[data-authority-actions]").forEach(function (root) {
    var product = root.getAttribute("data-product") || "Research";
    var title = root.getAttribute("data-title") || document.title;
    var url = canonical(root);
    var accessed = new Date().toISOString().slice(0, 10);
    var citation = root.getAttribute("data-citation") ||
      product + ". " + title + ". " + url + " (accessed " + accessed + ").";
    var status = root.querySelector("[data-authority-status]");
    var timer = 0;
    var say = function (message) {
      if (!status) return;
      status.textContent = message;
      clearTimeout(timer);
      timer = setTimeout(function () { status.textContent = ""; }, 2200);
    };

    root.querySelectorAll("[data-authority-action]").forEach(function (control) {
      control.addEventListener("click", function (event) {
        var action = control.getAttribute("data-authority-action");
        if (control.tagName === "A") {
          emit(root, action);
          return;
        }
        event.preventDefault();
        if (action === "share_opened") {
          var shared = campaignUrl(url, product, "native");
          if (navigator.share) {
            navigator.share({title: title, text: title, url: shared})
              .then(function () { emit(root, action); say("share sheet opened"); })
              .catch(function (error) { if (!error || error.name !== "AbortError") say("share unavailable"); });
          } else {
            copy(shared).then(function (ok) { if (ok) emit(root, action); say(ok ? "share link copied" : "copy blocked"); });
          }
        } else if (action === "link_copied") {
          copy(campaignUrl(url, product, "copy_link")).then(function (ok) { if (ok) emit(root, action); say(ok ? "tracked link copied" : "copy blocked"); });
        } else if (action === "citation_copied") {
          copy(citation).then(function (ok) { if (ok) emit(root, action); say(ok ? "citation copied" : "copy blocked"); });
        } else if (action === "share_card_downloaded") {
          downloadCard(root);
          emit(root, action);
          say("citation card saved");
        }
      });
    });
  });
}());
