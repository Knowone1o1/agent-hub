// Cartoon characters, drawn from parts so every agent is recognisable beyond colour:
// skin, hairstyle + hair colour, eyes, mouth, glasses, facial hair, hat and outfit.
// Looks come from (1) a `look:` line in the agent's .agents/<id>.md, else (2) the starter cast below,
// else (3) the role (researcher -> deerstalker, data -> hard hat, ...), else (4) a stable hash of the id.
(function () {
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const INK = "#2a1d33", SW = 2.6;                       // outline colour + width, matching the room art
  const SKIN = ["#ffdcc2", "#f6c79e", "#e2a877", "#c68a5a", "#9a6440", "#6e4630"];
  const HAIRC = ["#2b1d14", "#5a3825", "#a0522d", "#e3b25c", "#e8607d", "#7a5cff", "#1f2937", "#d9d9e3", "#ff8a3d", "#2fb8a6"];
  const HAIRS = ["short", "spiky", "bob", "bun", "long", "curly", "ponytail", "buzz", "mohawk"];
  const HATS = ["none", "beret", "cap", "headphones", "beanie", "bow", "none", "hardhat"];
  const OUTFITS = ["hoodie", "jacket", "turtleneck", "vest", "labcoat", "smock", "blazer", "sweater"];
  const EYES = ["round", "happy", "wide", "lashes"];
  const MOUTHS = ["smile", "grin", "smirk", "open"];
  const GLASSES = ["none", "none", "none", "round", "square"];
  const EXTRAS = ["none", "freckles", "blush", "none", "mustache", "beard"];

  // the starter cast
  const CAST = {
    principal: { skin: 2, hair: "short", hairColor: "#4a3426", hat: "explorer", eyes: "happy", mouth: "smile", glasses: "none", extra: "mustache", outfit: "vest" },
    design:    { skin: 0, hair: "bob", hairColor: "#7a5cff", hat: "beret", eyes: "lashes", mouth: "grin", glasses: "round", extra: "blush", outfit: "smock" },
    algorithm: { skin: 3, hair: "bun", hairColor: "#1f1a17", hat: "none", eyes: "round", mouth: "smirk", glasses: "square", extra: "freckles", outfit: "turtleneck" },
    code:      { skin: 1, hair: "spiky", hairColor: "#ff8a3d", hat: "headphones", eyes: "wide", mouth: "grin", glasses: "none", extra: "none", outfit: "hoodie" },
    judge:     { skin: 4, hair: "none", hairColor: "#f4f1ea", hat: "wig", eyes: "round", mouth: "smirk", glasses: "monocle", extra: "none", outfit: "blazer" },
  };
  // role keywords -> signature pieces for new recruits
  const ROLES = [
    [/research|analyst|scout|investigat/i, { hat: "deerstalker", outfit: "jacket" }],
    [/judge|critic|review|qa|quality|test/i, { hat: "wig", outfit: "blazer", glasses: "monocle" }],
    [/secur|audit|guard|safety/i, { hat: "helmet", outfit: "jacket", glasses: "square" }],
    [/data|architect|infra|devops|platform|backend/i, { hat: "hardhat", outfit: "vest" }],
    [/design|ux|ui|artist|brand|visual/i, { hat: "beret", outfit: "smock" }],
    [/writ|copy|doc|content|editor/i, { hat: "beanie", outfit: "sweater", glasses: "round" }],
    [/code|coder|engineer|developer|program|front|full/i, { hat: "cap", outfit: "hoodie" }],
    [/scien|math|algorithm|ml|ai /i, { outfit: "labcoat", glasses: "square" }],
    [/lead|manager|principal|director|product/i, { hat: "crown", outfit: "blazer" }],
  ];

  function hash(s) { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
  function pick(arr, h, salt) { return arr[(hash(h + ":" + salt)) % arr.length]; }
  function parseLook(s) {
    const o = {};
    String(s || "").split(/[,;]/).forEach(kv => { const m = kv.split(/[:=]/); if (m.length === 2) o[m[0].trim()] = m[1].trim(); });
    if (o.skin && /^\d$/.test(o.skin)) o.skin = +o.skin;
    return o;
  }
  const cache = {};
  function look(a) {
    const key = (a.id || a.name || "x") + "|" + (a.look || "") + "|" + (a.role || "");
    if (cache[key]) return cache[key];
    const id = a.id || a.name || "x";
    let L = { skin: hash(id) % SKIN.length, hair: pick(HAIRS, id, "hair"), hairColor: pick(HAIRC, id, "hc"), hat: pick(HATS, id, "hat"),
              eyes: pick(EYES, id, "eyes"), mouth: pick(MOUTHS, id, "mouth"), glasses: pick(GLASSES, id, "gl"), extra: pick(EXTRAS, id, "ex"),
              outfit: pick(OUTFITS, id, "out") };
    const role = ROLES.find(([re]) => re.test(a.role || ""));
    if (role) L = { ...L, ...role[1] };
    if (CAST[id]) L = { ...L, ...CAST[id] };
    L = { ...L, ...parseLook(a.look) };
    if (L.hat === "wig") L.hair = "none";
    return (cache[key] = L);
  }
  function shade(hex, f) {
    if (!/^#[0-9a-f]{6}$/i.test(hex)) return hex;
    const n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    const m = v => Math.max(0, Math.min(255, Math.round(f > 0 ? v + (255 - v) * f : v * (1 + f))));
    return `rgb(${m(r)},${m(g)},${m(b)})`;
  }
  const P = (d, fill, extra = "") => `<path d="${d}" fill="${fill}" stroke="${INK}" stroke-width="${SW}" stroke-linejoin="round" stroke-linecap="round" ${extra}/>`;

  // ---------------------------------------------------------------- head parts (viewBox 0 0 100 100, face centred at 50,58)
  function hairBack(L) {
    const c = L.hairColor;
    switch (L.hair) {
      case "bob": return P("M19 78 Q14 24 50 23 Q86 24 81 78 Q71 82 69 70 L31 70 Q29 82 19 78Z", c);
      case "long": return P("M18 96 Q12 24 50 22 Q88 24 82 96 Q74 98 71 86 L29 86 Q26 98 18 96Z", c);
      case "ponytail": return P("M74 40 Q94 44 90 72 Q88 84 80 86 Q86 70 78 56Z", c);
      case "curly": return [[24, 44], [30, 32], [42, 25], [58, 25], [70, 32], [76, 44], [22, 58], [78, 58]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r="10" fill="${c}" stroke="${INK}" stroke-width="${SW}"/>`).join("");
      default: return "";
    }
  }
  function hairFront(L) {
    const c = L.hairColor, hi = shade(c, .25);
    switch (L.hair) {
      case "short": return P("M23 56 Q21 28 50 27 Q79 28 77 56 Q71 41 58 43 Q50 36 42 43 Q29 41 23 56Z", c) + `<path d="M36 33 Q46 29 56 31" stroke="${hi}" stroke-width="3" fill="none" stroke-linecap="round"/>`;
      case "spiky": return P("M24 54 L23 34 L32 40 L34 22 L43 35 L50 17 L57 35 L66 22 L68 40 L77 34 L76 54 Q66 43 50 44 Q34 43 24 54Z", c);
      case "bob": return P("M24 52 Q25 30 50 29 Q75 30 76 52 Q66 40 56 45 Q50 36 42 45 Q30 41 24 52Z", c);
      case "bun": return `<circle cx="50" cy="21" r="10" fill="${c}" stroke="${INK}" stroke-width="${SW}"/>` + P("M24 54 Q23 29 50 28 Q77 29 76 54 Q68 40 50 40 Q32 40 24 54Z", c);
      case "long": return P("M24 54 Q24 29 50 28 Q76 29 76 54 Q70 38 52 40 L50 33 L48 40 Q30 38 24 54Z", c);
      case "curly": return P("M25 50 Q28 32 50 31 Q72 32 75 50 Q64 41 50 42 Q36 41 25 50Z", c);
      case "ponytail": return P("M23 55 Q22 29 50 28 Q78 29 77 55 Q72 40 50 41 Q30 41 23 55Z", c) + `<circle cx="77" cy="42" r="4.5" fill="#ff5d8f" stroke="${INK}" stroke-width="2"/>`;
      case "buzz": return `<path d="M24 52 Q23 30 50 29 Q77 30 76 52 Q64 42 50 42 Q36 42 24 52Z" fill="${c}" opacity=".55"/>`;
      case "mohawk": return P("M44 44 L42 16 L50 10 L58 16 L56 44 Q50 40 44 44Z", c);
      default: return "";
    }
  }
  function eyes(L, awake) {
    if (!awake) return `<g stroke="${INK}" stroke-width="3" fill="none" stroke-linecap="round"><path d="M33 60 q6 5 12 0"/><path d="M55 60 q6 5 12 0"/></g>`;
    switch (L.eyes) {
      case "happy": return `<g stroke="${INK}" stroke-width="3.2" fill="none" stroke-linecap="round"><path d="M33 61 q6 -7 12 0"/><path d="M55 61 q6 -7 12 0"/></g>`;
      case "wide": return `<g class="eyes"><ellipse cx="39" cy="58" rx="6.5" ry="7.5" fill="#fff" stroke="${INK}" stroke-width="2"/><ellipse cx="61" cy="58" rx="6.5" ry="7.5" fill="#fff" stroke="${INK}" stroke-width="2"/>
        <circle cx="40" cy="59" r="3.6" fill="${INK}"/><circle cx="62" cy="59" r="3.6" fill="${INK}"/><circle cx="41.4" cy="57.4" r="1.3" fill="#fff"/><circle cx="63.4" cy="57.4" r="1.3" fill="#fff"/></g>`;
      case "lashes": return `<g class="eyes"><ellipse cx="39" cy="59" rx="4.4" ry="5.6" fill="${INK}"/><ellipse cx="61" cy="59" rx="4.4" ry="5.6" fill="${INK}"/>
        <circle cx="40.4" cy="57" r="1.6" fill="#fff"/><circle cx="62.4" cy="57" r="1.6" fill="#fff"/>
        <path d="M34 54 l-3 -3 M36.5 52.6 l-1.5 -3.6 M66 54 l3 -3 M63.5 52.6 l1.5 -3.6" stroke="${INK}" stroke-width="2" stroke-linecap="round"/></g>`;
      default: return `<g class="eyes"><ellipse cx="39" cy="59" rx="4.4" ry="5.6" fill="${INK}"/><ellipse cx="61" cy="59" rx="4.4" ry="5.6" fill="${INK}"/>
        <circle cx="40.4" cy="57" r="1.6" fill="#fff"/><circle cx="62.4" cy="57" r="1.6" fill="#fff"/></g>`;
    }
  }
  function mouth(L, awake) {
    if (!awake) return `<ellipse cx="50" cy="74" rx="3.4" ry="2.6" fill="${INK}" opacity=".8"/>`;
    switch (L.mouth) {
      case "grin": return P("M40 70 Q50 82 60 70 Z", "#fff") + `<path d="M43 74 Q50 77 57 74" stroke="#ff8fa3" stroke-width="2.4" fill="none"/>`;
      case "smirk": return `<path d="M43 73 Q52 77 59 70" stroke="${INK}" stroke-width="3" fill="none" stroke-linecap="round"/>`;
      case "open": return `<ellipse cx="50" cy="73" rx="5" ry="4.4" fill="${INK}"/><ellipse cx="50" cy="75" rx="3" ry="1.8" fill="#ff8fa3"/>`;
      default: return `<path d="M42 71 Q50 78 58 71" stroke="${INK}" stroke-width="3" fill="none" stroke-linecap="round"/>`;
    }
  }
  function glasses(L) {
    switch (L.glasses) {
      case "round": return `<g fill="rgba(255,255,255,.18)" stroke="${INK}" stroke-width="2.4"><circle cx="39" cy="59" r="8.5"/><circle cx="61" cy="59" r="8.5"/><path d="M47.5 58 Q50 56 52.5 58" fill="none"/></g>`;
      case "square": return `<g fill="rgba(255,255,255,.18)" stroke="${INK}" stroke-width="2.4"><rect x="30" y="52" width="17" height="13" rx="3"/><rect x="53" y="52" width="17" height="13" rx="3"/><path d="M47 57 L53 57" fill="none"/></g>`;
      case "monocle": return `<g stroke="#c9a227" stroke-width="2.6" fill="rgba(255,255,255,.2)"><circle cx="61" cy="59" r="9"/><path d="M69 64 Q74 76 70 88" fill="none" stroke-width="1.6"/></g>`;
      default: return "";
    }
  }
  function extra(L) {
    switch (L.extra) {
      case "freckles": return `<g fill="#b76e4b" opacity=".7"><circle cx="33" cy="67" r="1.2"/><circle cx="36" cy="69" r="1.2"/><circle cx="31" cy="70" r="1.1"/><circle cx="67" cy="67" r="1.2"/><circle cx="64" cy="69" r="1.2"/><circle cx="69" cy="70" r="1.1"/></g>`;
      case "blush": return `<ellipse cx="32" cy="68" rx="5" ry="3" fill="#ff7aa8" opacity=".45"/><ellipse cx="68" cy="68" rx="5" ry="3" fill="#ff7aa8" opacity=".45"/>`;
      case "mustache": return P("M38 69 Q44 64 50 68 Q56 64 62 69 Q56 72 50 70 Q44 72 38 69Z", L.hairColor === "#d9d9e3" ? "#d9d9e3" : shade(L.hairColor, -.1));
      case "beard": return P("M24 62 Q26 90 50 92 Q74 90 76 62 Q70 80 50 80 Q30 80 24 62Z", L.hairColor);
      default: return "";
    }
  }
  function hat(L, c) {
    const d = shade(c, -.3), l = shade(c, .3);
    switch (L.hat) {
      case "beret": return P("M20 36 Q22 16 52 15 Q82 17 80 34 Q66 30 50 31 Q32 31 20 36Z", c) + P("M50 15 L52 8", "none") + `<circle cx="52" cy="8" r="3" fill="${c}" stroke="${INK}" stroke-width="2"/>`;
      case "cap": return P("M24 42 Q24 18 50 18 Q76 18 76 42 Z", c) + P("M70 40 Q86 38 94 44 Q86 48 72 45Z", d) + `<circle cx="50" cy="19" r="3" fill="${l}" stroke="${INK}" stroke-width="2"/>`;
      case "headphones": return `<path d="M21 58 Q18 18 50 17 Q82 18 79 58" fill="none" stroke="${INK}" stroke-width="8" stroke-linecap="round"/><path d="M21 58 Q18 18 50 17 Q82 18 79 58" fill="none" stroke="${d}" stroke-width="4.5" stroke-linecap="round"/>`
        + `<rect x="13" y="50" width="13" height="20" rx="6" fill="${c}" stroke="${INK}" stroke-width="${SW}"/><rect x="74" y="50" width="13" height="20" rx="6" fill="${c}" stroke="${INK}" stroke-width="${SW}"/>`;
      case "beanie": return P("M22 44 Q22 16 50 15 Q78 16 78 44 Z", c) + `<rect x="20" y="38" width="60" height="10" rx="4" fill="${d}" stroke="${INK}" stroke-width="${SW}"/><circle cx="50" cy="12" r="6" fill="${l}" stroke="${INK}" stroke-width="2"/>`;
      case "bow": return P("M60 28 L72 20 L72 36 Z", c) + P("M60 28 L48 20 L48 36 Z", c) + `<circle cx="60" cy="28" r="3.5" fill="${d}" stroke="${INK}" stroke-width="2"/>`;
      case "hardhat": return P("M22 42 Q22 14 50 14 Q78 14 78 42 Z", "#ffcc33") + `<rect x="16" y="38" width="68" height="8" rx="4" fill="#f2b400" stroke="${INK}" stroke-width="${SW}"/><path d="M50 15 L50 38" stroke="#e0a800" stroke-width="5"/>`;
      case "helmet": return P("M20 50 Q20 12 50 12 Q80 12 80 50 L74 50 Q72 26 50 24 Q28 26 26 50Z", "#5b6b85") + `<rect x="28" y="46" width="44" height="8" rx="3" fill="rgba(120,200,255,.55)" stroke="${INK}" stroke-width="2"/>`;
      case "explorer": return P("M10 40 Q50 30 90 40 Q86 46 50 44 Q14 46 10 40Z", "#c8a464") + P("M26 40 Q26 12 50 12 Q74 12 74 40 Z", "#e0c27f") + `<rect x="26" y="33" width="48" height="6" fill="#7a5a2a" stroke="${INK}" stroke-width="2"/>`;
      case "deerstalker": return P("M22 44 Q22 15 50 14 Q78 15 78 44 Z", "#a07850") + P("M22 44 L16 58 L28 50 Z", "#8a6440") + P("M78 44 L84 58 L72 50 Z", "#8a6440")
        + `<path d="M30 24 L70 24 M26 34 L74 34" stroke="#6d4f30" stroke-width="1.6"/><circle cx="50" cy="13" r="3" fill="#6d4f30"/>`;
      case "crown": return P("M28 34 L30 14 L40 26 L50 10 L60 26 L70 14 L72 34 Z", "#ffcf3f") + `<circle cx="50" cy="22" r="3" fill="#ff5d8f" stroke="${INK}" stroke-width="1.6"/>`;
      case "wig": return [[24, 40], [22, 54], [24, 68], [28, 80], [76, 40], [78, 54], [76, 68], [72, 80], [32, 28], [44, 22], [56, 22], [68, 28]]
        .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="9" fill="#f4f1ea" stroke="${INK}" stroke-width="2.2"/>`).join("");
      default: return "";
    }
  }
  function head(a, awake) {
    const L = look(a), skin = SKIN[L.skin % SKIN.length] || SKIN[0], c = /^#[0-9a-f]{6}$/i.test(a.color || "") ? a.color : "#60a5fa";
    const wigBehind = L.hat === "wig" ? hat(L, c) : "";
    return `${wigBehind}${hairBack(L)}
      <circle cx="23" cy="61" r="6" fill="${skin}" stroke="${INK}" stroke-width="${SW}"/><circle cx="77" cy="61" r="6" fill="${skin}" stroke="${INK}" stroke-width="${SW}"/>
      <ellipse cx="50" cy="59" rx="28" ry="29" fill="${skin}" stroke="${INK}" stroke-width="${SW}"/>
      <ellipse cx="42" cy="44" rx="10" ry="5" fill="#fff" opacity=".22"/>
      ${extra(L)}${eyes(L, awake)}${glasses(L)}${mouth(L, awake)}${hairFront(L)}${L.hat === "wig" ? "" : hat(L, c)}`;
  }

  // public: head-and-shoulders avatar (cards, sidebar, drawer, bench) with the agent's emoji as a badge
  window.avatarSVG = function (a, awake, opts) {
    const c = /^#[0-9a-f]{6}$/i.test(a.color || "") ? a.color : "#60a5fa", badge = !(opts && opts.badge === false);
    return `<svg class="face ${awake ? "awake" : "asleep"}" viewBox="0 -6 100 112" aria-hidden="true" overflow="visible">
      <circle class="aura" cx="50" cy="56" r="48" fill="${c}"/>
      ${head(a, awake)}
      ${badge ? `<circle cx="82" cy="86" r="13" fill="#15151c" stroke="${c}" stroke-width="2.5"/><text x="82" y="91.5" font-size="14" text-anchor="middle">${esc(a.avatar || "🤖")}</text>` : ""}
    </svg>`;
  };
  window.headSVG = (a, awake) => `<svg class="face ${awake ? "awake" : "asleep"}" viewBox="0 -4 100 104" overflow="visible" aria-hidden="true">${head(a, awake)}</svg>`;

  // public: the body (torso + arms) for walkers in the studio room, viewBox 0 0 100 64
  window.bodySVG = function (a) {
    const L = look(a), c = /^#[0-9a-f]{6}$/i.test(a.color || "") ? a.color : "#60a5fa", d = shade(c, -.28), l = shade(c, .35);
    const skin = SKIN[L.skin % SKIN.length] || SKIN[0];
    const torso = "M24 14 Q50 4 76 14 L80 60 Q50 66 20 60 Z";
    let sleeve = c, inner = "";
    switch (L.outfit) {
      case "hoodie": inner = P(torso, c) + `<path d="M34 12 Q50 24 66 12" fill="${d}" stroke="${INK}" stroke-width="2"/><path d="M45 20 L44 32 M55 20 L56 32" stroke="#fff" stroke-width="2"/>` + P("M34 42 L66 42 L62 54 L38 54 Z", d); break;
      case "jacket": inner = P(torso, c) + `<path d="M50 10 L50 62" stroke="${INK}" stroke-width="2"/><path d="M50 14 L50 60" stroke="#e5e5e5" stroke-width="1.4" stroke-dasharray="2 3"/>` + P("M28 30 L40 30 L40 36 L28 36 Z", d) + P("M60 30 L72 30 L72 36 L60 36 Z", d); break;
      case "turtleneck": inner = P(torso, c) + `<rect x="38" y="4" width="24" height="10" rx="4" fill="${d}" stroke="${INK}" stroke-width="2"/>` + `<path d="M32 26 L68 26 M30 38 L70 38 M29 50 L71 50" stroke="${d}" stroke-width="1.6" opacity=".6"/>`; break;
      case "vest": sleeve = "#f4f1ea"; inner = P(torso, "#f4f1ea") + P("M26 16 L46 18 L44 60 L22 58 Z", c) + P("M74 16 L54 18 L56 60 L78 58 Z", c) + P("M47 14 L53 14 L55 36 L50 42 L45 36 Z", "#c0392b"); break;
      case "labcoat": sleeve = "#f8f8fb"; inner = P(torso, "#f8f8fb") + P("M44 10 L56 10 L54 40 L46 40 Z", c) + `<path d="M50 40 L50 62" stroke="${INK}" stroke-width="2"/><rect x="58" y="24" width="10" height="9" rx="1.5" fill="#fff" stroke="${INK}" stroke-width="1.6"/><path d="M61 24 L61 20" stroke="#3b82f6" stroke-width="2"/>`; break;
      case "smock": inner = P(torso, c) + P("M33 16 L67 16 L70 60 L30 60 Z", "#fffaf0") + ["#f472b6", "#60a5fa", "#fbbf24", "#34d399"].map((p, i) => `<circle cx="${40 + i * 7}" cy="${30 + (i % 2) * 12}" r="3" fill="${p}"/>`).join(""); break;
      case "blazer": inner = P(torso, "#2f2a44") + P("M40 8 L60 8 L56 40 L50 46 L44 40 Z", "#f4f1ea") + P("M40 10 L50 30 L36 34 Z", "#3e3858") + P("M60 10 L50 30 L64 34 Z", "#3e3858")
        + P("M43 14 L50 18 L57 14 L57 22 L50 18 L43 22 Z", c); sleeve = "#2f2a44"; break;
      case "sweater": inner = P(torso, c) + `<path d="M24 30 L76 30" stroke="${l}" stroke-width="5"/><path d="M24 30 L76 30" stroke="${INK}" stroke-width="0.6" opacity=".4"/>` + `<path d="M36 10 Q50 20 64 10" fill="none" stroke="${d}" stroke-width="3"/>`; break;
      default: inner = P(torso, c);
    }
    return `<svg class="body" viewBox="0 0 100 70" overflow="visible" aria-hidden="true">
      <g class="arm arm-l">${P("M24 16 Q12 20 12 40 L12 52 L22 52 L24 30 Z", sleeve)}<circle cx="17" cy="56" r="6" fill="${skin}" stroke="${INK}" stroke-width="${SW}"/></g>
      <g class="arm arm-r">${P("M76 16 Q88 20 88 40 L88 52 L78 52 L76 30 Z", sleeve)}<circle cx="83" cy="56" r="6" fill="${skin}" stroke="${INK}" stroke-width="${SW}"/></g>
      ${inner}</svg>`;
  };
  window.pantsColor = function (a) {
    const o = look(a).outfit;
    return { blazer: "#2f2a44", labcoat: "#4b5563", vest: "#3f3a5a", smock: "#6b5b95", hoodie: "#2d3a5a", jacket: "#374151", turtleneck: "#3a3047", sweater: "#4b4237" }[o] || "#2d2b3a";
  };
  window.agentLook = look;
})();
