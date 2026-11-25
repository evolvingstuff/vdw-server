/* The VitaminDWiki AI chat. Runs on the chat subdomain, usually in a frame on a wiki page.

   The reader's API key and conversation stay in this browser: the key goes straight to the
   AI provider they chose, and the only requests to VitaminDWiki are config.json (the
   assistant's instructions and tools) and /tool (searching and reading wiki pages).

   No inline scripts or HTML strings: the page's Content-Security-Policy forbids them, and
   model answers are untrusted text, so everything is built as DOM nodes. */
(function () {
    'use strict';

    const MAX_TOOL_ROUNDS = 10;      // model -> tools -> model ... per question
    const HISTORY_SENT = 21;         // earlier messages sent with a question (ends on the new one)
    const HISTORY_KEPT = 60;         // messages kept in this browser
    const MAX_ANSWER_TOKENS = 8000;

    const PROVIDERS = {
        anthropic: {name: 'Claude', keyUrl: 'https://platform.claude.com/settings/keys', keyLink: 'Get a Claude API key from Anthropic'},
        openai: {name: 'OpenAI', keyUrl: 'https://platform.openai.com/api-keys', keyLink: 'Get an API key from OpenAI'},
        gemini: {name: 'Gemini', keyUrl: 'https://aistudio.google.com/apikey', keyLink: 'Get a Gemini API key from Google'},
    };

    // --- storage (falls back to memory when the browser blocks it) --------------------

    function makeStore(getStorage) {
        const memory = {};
        return {
            get(name) {
                try { const value = getStorage().getItem(name); if (value !== null) return value; } catch (error) { /* blocked */ }
                return Object.prototype.hasOwnProperty.call(memory, name) ? memory[name] : null;
            },
            set(name, value) {
                memory[name] = value;
                try { getStorage().setItem(name, value); } catch (error) { /* blocked or full */ }
            },
            remove(name) {
                delete memory[name];
                try { getStorage().removeItem(name); } catch (error) { /* blocked */ }
            },
        };
    }
    const kept = makeStore(() => window.localStorage);       // survives closing the browser
    const session = makeStore(() => window.sessionStorage);  // gone when the tab closes

    function readJson(store, name, fallback) {
        try { const value = JSON.parse(store.get(name)); return value === null ? fallback : value; } catch (error) { return fallback; }
    }

    const keyName = (provider) => `vdw-chat-key-${provider}`;
    const getKey = (provider) => kept.get(keyName(provider)) || session.get(keyName(provider)) || '';
    function saveKey(provider, key, remember) {
        kept.remove(keyName(provider));
        session.remove(keyName(provider));
        (remember ? kept : session).set(keyName(provider), key);
    }
    function forgetKey(provider) {
        kept.remove(keyName(provider));
        session.remove(keyName(provider));
    }

    // --- state --------------------------------------------------------------------------

    let config = null;                // from config.json
    let prefs = readJson(kept, 'vdw-chat-prefs', {});  // {provider, models: {provider: modelId}, remember}
    if (typeof prefs !== 'object' || Array.isArray(prefs)) prefs = {};
    prefs.models = prefs.models || {};
    let history = loadHistory();      // [{role: 'user'|'assistant', text}]
    let page = null;                  // the wiki page the chat is shown on: {title, url, slug}
    let parentOrigin = '';
    let running = null;               // AbortController while a question is being answered
    const modelLists = {};            // `${provider}:${key}` -> [{id, name}]
    const embedded = window.parent !== window;

    function loadHistory() {
        const saved = readJson(kept, 'vdw-chat-history', []);
        if (!Array.isArray(saved) || saved.length % 2) return [];
        const valid = saved.every((message, index) =>
            message && typeof message.text === 'string' && message.role === (index % 2 ? 'assistant' : 'user'));
        return valid ? saved : [];
    }

    function saveHistory() {
        if (history.length > HISTORY_KEPT) history = history.slice(-HISTORY_KEPT);
        kept.set('vdw-chat-history', JSON.stringify(history));
    }

    const savePrefs = () => kept.set('vdw-chat-prefs', JSON.stringify(prefs));

    // --- small DOM helpers --------------------------------------------------------------

    const $ = (id) => document.getElementById(id);

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    // --- Markdown -> DOM ------------------------------------------------------------------

    const INLINE_RE = new RegExp([
        '(`[^`\\n]+`)',                                         // 1 code
        '\\*\\*([^\\n]+?)\\*\\*',                               // 2 bold
        '__([^\\n]+?)__',                                       // 3 bold
        '\\*([^\\s*][^*\\n]*?)\\*',                             // 4 italic
        '\\b_([^_\\n]+?)_\\b',                                  // 5 italic
        '\\[([^\\]\\n]+)\\]\\(([^)\\s]+)(?:\\s+"[^"]*")?\\)',   // 6 link text, 7 link address
        '(https?:\\/\\/[^\\s<>()\\[\\]]+)',                     // 8 bare address
    ].join('|'), 'g');

    function linkNode(address, label) {
        let url;
        try { url = new URL(address); } catch (error) { return null; }
        if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
        const link = el('a');
        const onWiki = config && url.origin === config.wiki_origin;
        if (onWiki && parentOrigin) {
            // Open wiki pages in the window the chat sits in (on a local copy of the site, that copy).
            const parent = new URL(parentOrigin);
            url.protocol = parent.protocol;
            url.host = parent.host;
            link.target = '_top';
        } else {
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
        }
        link.href = url.href;
        if (label === undefined) link.textContent = address; else renderInline(label, link);
        return link;
    }

    function renderInline(text, parent) {
        let last = 0;
        INLINE_RE.lastIndex = 0;
        const matches = Array.from(text.matchAll(INLINE_RE));
        for (const match of matches) {
            if (match.index > last) parent.append(text.slice(last, match.index));
            last = match.index + match[0].length;
            if (match[1]) {
                parent.append(el('code', '', match[1].slice(1, -1)));
            } else if (match[2] !== undefined || match[3] !== undefined) {
                renderInline(match[2] !== undefined ? match[2] : match[3], parent.appendChild(el('strong')));
            } else if (match[4] !== undefined || match[5] !== undefined) {
                renderInline(match[4] !== undefined ? match[4] : match[5], parent.appendChild(el('em')));
            } else if (match[6] !== undefined) {
                const link = linkNode(match[7], match[6]);
                if (link) parent.append(link); else parent.append(match[0]);
            } else {
                const trailing = /[.,;:!?'"]+$/.exec(match[8]);
                const address = trailing ? match[8].slice(0, -trailing[0].length) : match[8];
                const link = linkNode(address);
                if (link) parent.append(link); else parent.append(address);
                if (trailing) parent.append(trailing[0]);
            }
        }
        if (last < text.length) parent.append(text.slice(last));
    }

    const LIST_ITEM_RE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
    const HEADING_RE = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
    const RULE_RE = /^\s*([-*_])(\s*\1){2,}\s*$/;
    const FENCE_RE = /^\s*```/;
    const QUOTE_RE = /^\s*>\s?/;
    const TABLE_DIVIDER_RE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

    const isBlank = (line) => /^\s*$/.test(line);
    const indentOf = (line) => /^\s*/.exec(line)[0].length;
    const tableStartsAt = (lines, i) =>
        lines[i].includes('|') && i + 1 < lines.length && lines[i + 1].includes('|') && TABLE_DIVIDER_RE.test(lines[i + 1]);
    const startsBlock = (lines, i) =>
        FENCE_RE.test(lines[i]) || HEADING_RE.test(lines[i]) || RULE_RE.test(lines[i]) || QUOTE_RE.test(lines[i]) ||
        LIST_ITEM_RE.test(lines[i]) || tableStartsAt(lines, i);

    function tableCells(line) {
        return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
    }

    function renderList(lines, start, parent) {
        const base = indentOf(lines[start]);
        const numbered = (line) => /^\s*\d/.test(line);
        const ordered = numbered(lines[start]);
        const list = parent.appendChild(el(ordered ? 'ol' : 'ul'));
        let i = start;
        while (i < lines.length) {
            const item = LIST_ITEM_RE.exec(lines[i]);
            if (!item || item[1].length > base + 1 || item[1].length < base || numbered(lines[i]) !== ordered) break;
            const node = list.appendChild(el('li'));
            renderInline(item[3], node);
            i += 1;
            const nested = [];   // deeper-indented lines belong to this item
            while (i < lines.length) {
                if (!isBlank(lines[i]) && indentOf(lines[i]) > base + 1) { nested.push(lines[i]); i += 1; continue; }
                if (isBlank(lines[i]) && i + 1 < lines.length && !isBlank(lines[i + 1]) && indentOf(lines[i + 1]) > base + 1) {
                    nested.push(''); i += 1; continue;
                }
                break;
            }
            if (nested.length) {
                const cut = Math.min(...nested.filter((line) => !isBlank(line)).map(indentOf));
                renderBlocks(nested.map((line) => line.slice(cut)), node);
            }
            // A blank line between two items of the same list doesn't end it.
            if (i + 1 < lines.length && isBlank(lines[i]) && LIST_ITEM_RE.test(lines[i + 1]) && indentOf(lines[i + 1]) === base &&
                numbered(lines[i + 1]) === ordered) i += 1;
        }
        return i;
    }

    function renderBlocks(lines, parent) {
        let i = 0;
        while (i < lines.length) {
            const line = lines[i];
            let match;
            if (isBlank(line)) {
                i += 1;
            } else if (FENCE_RE.test(line)) {
                const code = [];
                i += 1;
                while (i < lines.length && !FENCE_RE.test(lines[i])) { code.push(lines[i]); i += 1; }
                i += 1;
                parent.appendChild(el('pre')).appendChild(el('code', '', code.join('\n')));
            } else if ((match = HEADING_RE.exec(line))) {
                // The panel is narrow and has its own title, so answer headings start at h3.
                renderInline(match[2], parent.appendChild(el(`h${Math.min(match[1].length + 2, 5)}`)));
                i += 1;
            } else if (RULE_RE.test(line)) {
                parent.appendChild(el('hr'));
                i += 1;
            } else if (QUOTE_RE.test(line)) {
                const quoted = [];
                while (i < lines.length && QUOTE_RE.test(lines[i])) { quoted.push(lines[i].replace(QUOTE_RE, '')); i += 1; }
                renderBlocks(quoted, parent.appendChild(el('blockquote')));
            } else if (tableStartsAt(lines, i)) {
                const table = parent.appendChild(el('div', 'table-wrap')).appendChild(el('table'));
                const head = table.appendChild(el('thead')).appendChild(el('tr'));
                tableCells(line).forEach((cell) => renderInline(cell, head.appendChild(el('th'))));
                const body = table.appendChild(el('tbody'));
                i += 2;
                while (i < lines.length && !isBlank(lines[i]) && lines[i].includes('|')) {
                    const row = body.appendChild(el('tr'));
                    tableCells(lines[i]).forEach((cell) => renderInline(cell, row.appendChild(el('td'))));
                    i += 1;
                }
            } else if (LIST_ITEM_RE.test(line)) {
                i = renderList(lines, i, parent);
            } else {
                const text = [line.trim()];
                i += 1;
                while (i < lines.length && !isBlank(lines[i]) && !startsBlock(lines, i)) { text.push(lines[i].trim()); i += 1; }
                renderInline(text.join(' '), parent.appendChild(el('p')));
            }
        }
    }

    function renderMarkdown(text, parent) {
        renderBlocks(text.replace(/\r\n?/g, '\n').split('\n'), parent);
    }

    // --- talking to the AI providers --------------------------------------------------

    async function requestJson(url, options, who) {
        let response;
        try {
            response = await fetch(url, options);
        } catch (error) {
            if (error.name === 'AbortError') throw error;
            throw new Error(`Could not reach ${who}. Check your internet connection and try again.`);
        }
        let data = null;
        try { data = await response.json(); } catch (error) { /* not JSON */ }
        if (response.ok && data) return data;
        let detail = data && data.error;
        if (detail && typeof detail === 'object') detail = detail.message;
        detail = typeof detail === 'string' && detail ? ` ${who} said: “${detail}”` : '';
        if (response.status === 401 || response.status === 403) {
            throw new Error(`${who} did not accept the API key. Check the key in Settings.${detail}`);
        }
        if (response.status === 429) {
            throw new Error(`${who} is limiting your requests, or your account is out of credit.${detail}`);
        }
        throw new Error(`${who} returned an error (${response.status}).${detail}`);
    }

    const postJson = (url, headers, body, signal, who) => requestJson(url, {
        method: 'POST',
        headers: Object.assign({'content-type': 'application/json'}, headers),
        body: JSON.stringify(body),
        signal,
    }, who);

    function parseArguments(text) {
        try { const value = JSON.parse(text || '{}'); return value && typeof value === 'object' ? value : {}; } catch (error) { return {}; }
    }

    /* Each adapter:
         models(key, signal)      -> [{id, name}], best first
         start(turns, context)    -> the provider's own message list
         send(context, messages)  -> {text, calls: [{id, name, args}], note}; adds the reply to messages
         addResults(messages, results)   results: [{id, name, text, isError}]
       context: {key, model, system, tools, signal, final}; final means "answer now, no more tools". */
    const ADAPTERS = {
        anthropic: {
            headers(key) {
                return {
                    'x-api-key': key,
                    'anthropic-version': '2023-06-01',
                    'anthropic-dangerous-direct-browser-access': 'true',  // this is the reader's own key, in their browser
                };
            },
            async models(key, signal) {
                const data = await requestJson('https://api.anthropic.com/v1/models?limit=100', {headers: this.headers(key), signal}, 'Anthropic');
                const models = (data.data || []).map((model) => ({id: model.id, name: model.display_name || model.id}));
                const preferred = models.findIndex((model) => model.id === 'claude-opus-5-5');
                if (preferred > 0) models.unshift(models.splice(preferred, 1)[0]);
                return models;
            },
            start(turns) {
                return turns.map((turn) => ({role: turn.role, content: turn.text}));
            },
            async send(context, messages) {
                const body = {
                    model: context.model,
                    max_tokens: MAX_ANSWER_TOKENS,
                    system: [{type: 'text', text: context.system, cache_control: {type: 'ephemeral'}}],
                    tools: context.tools.map((tool) => ({name: tool.name, description: tool.description, input_schema: tool.parameters})),
                    messages,
                };
                if (context.final) body.tool_choice = {type: 'none'};
                const data = await postJson('https://api.anthropic.com/v1/messages', this.headers(context.key), body, context.signal, 'Anthropic');
                const blocks = data.content || [];
                messages.push({role: 'assistant', content: blocks});
                return {
                    text: blocks.filter((block) => block.type === 'text').map((block) => block.text).join(''),
                    calls: blocks.filter((block) => block.type === 'tool_use').map((block) => ({id: block.id, name: block.name, args: block.input})),
                    note: data.stop_reason === 'max_tokens' ? 'cut' : data.stop_reason === 'refusal' ? 'declined' : '',
                };
            },
            addResults(messages, results) {
                messages.push({
                    role: 'user',
                    content: results.map((result) => ({type: 'tool_result', tool_use_id: result.id, content: result.text, is_error: result.isError})),
                });
            },
        },

        openai: {
            headers(key) {
                return {authorization: `Bearer ${key}`};
            },
            async models(key, signal) {
                const data = await requestJson('https://api.openai.com/v1/models', {headers: this.headers(key), signal}, 'OpenAI');
                const chat = (data.data || []).map((model) => model.id).filter((id) =>
                    /^(gpt-|o\d|chatgpt-)/.test(id) &&
                    !/(audio|realtime|tts|transcribe|image|embedding|search|instruct|moderation|codex)/.test(id));
                // Plain names such as "gpt-5.2" first, highest version first; dated and variant names after.
                const version = (id) => { const match = /^gpt-(\d+(?:\.\d+)?)$/.exec(id); return match ? parseFloat(match[1]) : -1; };
                chat.sort((a, b) => version(b) - version(a) || (a < b ? 1 : -1));
                return chat.map((id) => ({id, name: id}));
            },
            start(turns, context) {
                return [{role: 'system', content: context.system}].concat(turns.map((turn) => ({role: turn.role, content: turn.text})));
            },
            async send(context, messages) {
                const body = {
                    model: context.model,
                    messages,
                    tools: context.tools.map((tool) => ({type: 'function', function: {name: tool.name, description: tool.description, parameters: tool.parameters}})),
                };
                if (context.final) body.tool_choice = 'none';
                const data = await postJson('https://api.openai.com/v1/chat/completions', this.headers(context.key), body, context.signal, 'OpenAI');
                const choice = (data.choices || [])[0];
                if (!choice || !choice.message) throw new Error('OpenAI returned no answer.');
                const calls = (choice.message.tool_calls || []).filter((call) => call.type === 'function');
                const reply = {role: 'assistant', content: choice.message.content || ''};
                if (calls.length) reply.tool_calls = calls;
                messages.push(reply);
                return {
                    text: choice.message.content || choice.message.refusal || '',
                    calls: calls.map((call) => ({id: call.id, name: call.function.name, args: parseArguments(call.function.arguments)})),
                    note: choice.finish_reason === 'length' ? 'cut' : choice.finish_reason === 'content_filter' ? 'declined' : '',
                };
            },
            addResults(messages, results) {
                results.forEach((result) => messages.push({role: 'tool', tool_call_id: result.id, content: result.text}));
            },
        },

        gemini: {
            headers(key) {
                return {'x-goog-api-key': key};
            },
            async models(key, signal) {
                const data = await requestJson('https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000', {headers: this.headers(key), signal}, 'Google');
                const models = (data.models || [])
                    .filter((model) => (model.supportedGenerationMethods || []).includes('generateContent'))
                    .map((model) => ({id: model.name.replace(/^models\//, ''), name: model.displayName || model.name}))
                    .filter((model) => /^gemini-/.test(model.id) && !/(embedding|image|tts|audio|live|robotics|computer-use)/.test(model.id));
                // Highest version first, and "pro" before the smaller models of the same version.
                const version = (id) => { const match = /^gemini-(\d+(?:\.\d+)?)/.exec(id); return match ? parseFloat(match[1]) : 0; };
                const rank = (id) => (/-pro/.test(id) ? 0 : 1);
                models.sort((a, b) => version(b.id) - version(a.id) || rank(a.id) - rank(b.id) || a.id.length - b.id.length);
                return models;
            },
            // Gemini accepts a subset of JSON Schema for tool parameters.
            schema(value) {
                if (Array.isArray(value)) return value.map((item) => this.schema(item));
                if (!value || typeof value !== 'object') return value;
                const copy = {};
                Object.keys(value).forEach((name) => {
                    if (name === 'additionalProperties' || name === 'default') return;
                    copy[name] = name === 'properties'
                        ? Object.fromEntries(Object.entries(value[name]).map(([property, schema]) => [property, this.schema(schema)]))
                        : this.schema(value[name]);
                });
                return copy;
            },
            start(turns) {
                return turns.map((turn) => ({role: turn.role === 'assistant' ? 'model' : 'user', parts: [{text: turn.text}]}));
            },
            async send(context, messages) {
                const body = {
                    systemInstruction: {parts: [{text: context.system}]},
                    contents: messages,
                    tools: [{functionDeclarations: context.tools.map((tool) => ({name: tool.name, description: tool.description, parameters: this.schema(tool.parameters)}))}],
                };
                if (context.final) body.toolConfig = {functionCallingConfig: {mode: 'NONE'}};
                const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(context.model)}:generateContent`;
                const data = await postJson(url, this.headers(context.key), body, context.signal, 'Google');
                const candidate = (data.candidates || [])[0];
                if (!candidate || !candidate.content) {
                    const reason = (candidate && candidate.finishReason) || (data.promptFeedback && data.promptFeedback.blockReason);
                    throw new Error(`Gemini returned no answer${reason ? ` (${reason})` : ''}.`);
                }
                const parts = candidate.content.parts || [];
                // The reply goes back unchanged: newer Gemini models need the signatures on its parts.
                messages.push({role: 'model', parts});
                return {
                    text: parts.filter((part) => typeof part.text === 'string' && !part.thought).map((part) => part.text).join(''),
                    calls: parts.filter((part) => part.functionCall).map((part) => ({
                        id: part.functionCall.id || '', name: part.functionCall.name, args: part.functionCall.args || {},
                    })),
                    note: candidate.finishReason === 'MAX_TOKENS' ? 'cut' : candidate.finishReason === 'SAFETY' ? 'declined' : '',
                };
            },
            addResults(messages, results) {
                messages.push({
                    role: 'user',
                    parts: results.map((result) => {
                        const response = {name: result.name, response: result.isError ? {error: result.text} : {result: result.text}};
                        if (result.id) response.id = result.id;
                        return {functionResponse: response};
                    }),
                });
            },
        },
    };

    // --- wiki tools ---------------------------------------------------------------------

    async function runTool(call, signal) {
        try {
            const response = await fetch('tool', {
                method: 'POST',
                headers: {'content-type': 'application/json'},
                body: JSON.stringify({name: call.name, arguments: call.args}),
                signal,
            });
            if (response.status === 429) return {text: 'VitaminDWiki is receiving too many requests. Wait a moment before trying again.', isError: true};
            const data = await response.json();
            return {text: String(data.text), isError: Boolean(data.is_error)};
        } catch (error) {
            if (error.name === 'AbortError') throw error;
            return {text: 'VitaminDWiki could not be reached.', isError: true};
        }
    }

    function describeCall(call) {
        const args = call.args || {};
        const quote = (value) => `“${String(value).slice(0, 80)}”`;
        switch (call.name) {
            case 'search': return `Searching for ${quote(args.query || '')}`;
            case 'fetch': return `Reading ${quote(args.id || '')}`;
            case 'continue_page': return `Reading more of ${quote(args.id || '')}`;
            case 'find_topics': return args.query ? `Looking up topics like ${quote(args.query)}` : 'Listing topics';
            case 'topic_pages': return `Listing pages on ${quote((Array.isArray(args.topics) ? args.topics : []).join(' + '))}`;
            case 'recent_pages': return 'Checking the newest pages';
            default: return `Using ${call.name}`;
        }
    }

    function systemText() {
        let text = config.instructions;
        if (page && page.title) {
            const title = String(page.title).slice(0, 200);
            text += `\n\nThe reader has this VitaminDWiki page open: "${title}"`;
            if (page.slug) text += ` (id: ${String(page.slug).slice(0, 200)})`;
            text += '. If they say "this page", they mean it';
            text += page.slug ? '; read it with fetch before answering about it.' : '.';
        }
        return text;
    }

    /* Answer the last message in `turns`, letting the model use the wiki tools. */
    async function answer(turns, signal, onStep) {
        const provider = prefs.provider;
        const adapter = ADAPTERS[provider];
        const context = {
            key: getKey(provider), model: prefs.models[provider], system: systemText(), tools: config.tools, signal, final: false,
        };
        const messages = adapter.start(turns, context);
        for (let round = 0; ; round += 1) {
            context.final = round >= MAX_TOOL_ROUNDS;
            onStep(round === 0 ? 'Thinking' : 'Reading what it found');
            const reply = await adapter.send(context, messages);
            if (!reply.calls.length || reply.note || context.final) {
                let text = reply.text.trim();
                if (reply.note === 'cut') text += '\n\n*(The answer was cut off because it got too long.)*';
                if (reply.note === 'declined' && !text) text = 'The AI model declined to answer this.';
                return text || 'The AI model gave no answer. Try asking in a different way.';
            }
            const results = [];
            for (const call of reply.calls) {
                onStep(describeCall(call));
                const result = await runTool(call, signal);
                results.push({id: call.id, name: call.name, text: result.text, isError: result.isError});
            }
            adapter.addResults(messages, results);
        }
    }

    // --- chat view ----------------------------------------------------------------------

    function scrollToEnd() {
        const messages = $('messages');
        messages.scrollTop = messages.scrollHeight;
    }

    function addMessage(role, text) {
        const node = el('div', `message ${role}`);
        if (role === 'assistant') renderMarkdown(text, node); else node.textContent = text;
        $('messages').appendChild(node);
        return node;
    }

    function showWelcome() {
        const welcome = el('div', 'welcome');
        welcome.appendChild(el('p', '', 'Ask a question and the AI will search VitaminDWiki and answer with links to the pages it used.'));
        const suggestions = welcome.appendChild(el('div', 'suggestions'));
        const questions = ['What does VitaminDWiki say about vitamin D and COVID?', 'What are the newest pages on VitaminDWiki?'];
        if (page && page.slug) questions.unshift('Summarize this page');
        questions.forEach((question) => {
            const button = suggestions.appendChild(el('button', '', question));
            button.type = 'button';
            button.addEventListener('click', () => ask(question));
        });
        $('messages').appendChild(welcome);
    }

    function renderConversation() {
        $('messages').replaceChildren();
        if (!history.length) showWelcome();
        history.forEach((message) => addMessage(message.role, message.text));
        scrollToEnd();
    }

    function setRunning(controller) {
        running = controller;
        $('send').textContent = controller ? 'Stop' : 'Send';
        $('new-chat').disabled = Boolean(controller);
        $('open-settings').disabled = Boolean(controller);
    }

    async function ask(text) {
        text = text.trim();
        if (!text || running) return;
        const welcome = $('messages').querySelector('.welcome');
        if (welcome) welcome.remove();
        $('messages').querySelectorAll('.problem').forEach((node) => node.remove());

        history.push({role: 'user', text});
        const question = addMessage('user', text);
        $('input').value = '';

        const working = el('div', 'working');
        const steps = working.appendChild(el('ul'));
        $('messages').appendChild(working);
        const onStep = (label) => {
            steps.querySelectorAll('.current').forEach((node) => node.classList.remove('current'));
            steps.appendChild(el('li', 'current', label));
            scrollToEnd();
        };

        const controller = new AbortController();
        setRunning(controller);
        try {
            const reply = await answer(history.slice(-HISTORY_SENT), controller.signal, onStep);
            history.push({role: 'assistant', text: reply});
            saveHistory();
            working.remove();
            const node = addMessage('assistant', reply);
            node.scrollIntoView({block: 'start'});
        } catch (error) {
            // The question goes back into the box so it can be sent again.
            history.pop();
            working.remove();
            question.remove();
            $('input').value = text;
            if (error.name !== 'AbortError') {
                addMessage('problem', error.message || 'Something went wrong. Please try again.');
                scrollToEnd();
            }
            if (!history.length && !$('messages').children.length) showWelcome();
        } finally {
            setRunning(null);
            $('input').focus();
        }
    }

    // --- setup view ---------------------------------------------------------------------

    const selectedProvider = () => {
        const checked = document.querySelector('input[name="provider"]:checked');
        return checked ? checked.value : 'anthropic';
    };

    function showSetupError(message) {
        $('setup-error').textContent = message || '';
        $('setup-error').hidden = !message;
    }

    function fillModels(provider, models) {
        const select = $('model');
        select.replaceChildren();
        models.forEach((model) => {
            const option = el('option', '', model.name === model.id ? model.id : `${model.name} (${model.id})`);
            option.value = model.id;
            select.appendChild(option);
        });
        const saved = prefs.models[provider];
        if (saved && models.some((model) => model.id === saved)) select.value = saved;
        $('model-row').hidden = !models.length;
    }

    async function loadModels(provider, key) {
        const cacheKey = `${provider}:${key}`;
        if (!modelLists[cacheKey]) {
            const models = await ADAPTERS[provider].models(key);
            if (!models.length) throw new Error(`${PROVIDERS[provider].name} listed no chat models for this key.`);
            modelLists[cacheKey] = models;
        }
        return modelLists[cacheKey];
    }

    /* Show the saved key and model list of the selected provider. */
    function showProvider() {
        const provider = selectedProvider();
        const key = getKey(provider);
        $('key').value = key;
        $('key-link').href = PROVIDERS[provider].keyUrl;
        $('key-link').textContent = PROVIDERS[provider].keyLink;
        $('forget').hidden = !key;
        $('model-row').hidden = true;
        showSetupError('');
        if (key) {
            loadModels(provider, key).then((models) => {
                if (selectedProvider() === provider && $('key').value === key) fillModels(provider, models);
            }).catch(() => { /* shown when they press Save */ });
        }
    }

    function showSetup() {
        const provider = prefs.provider && PROVIDERS[prefs.provider] ? prefs.provider : 'anthropic';
        document.querySelector(`input[name="provider"][value="${provider}"]`).checked = true;
        $('remember').checked = prefs.remember !== false;
        $('cancel-setup').hidden = !ready();
        showProvider();
        show('setup');
        $('key').focus();
    }

    async function saveSetup(event) {
        event.preventDefault();
        const provider = selectedProvider();
        const key = $('key').value.trim();
        if (!key) { showSetupError('Paste your API key first.'); return; }
        $('save').disabled = true;
        $('save').textContent = 'Checking the key…';
        showSetupError('');
        try {
            const models = await loadModels(provider, key);
            const listed = !$('model-row').hidden && models.some((model) => model.id === $('model').value);
            prefs.models[provider] = listed ? $('model').value : models[0].id;
            prefs.provider = provider;
            prefs.remember = $('remember').checked;
            savePrefs();
            saveKey(provider, key, prefs.remember);
            showChat();
        } catch (error) {
            showSetupError(error.message);
        } finally {
            $('save').disabled = false;
            $('save').textContent = 'Save and chat';
        }
    }

    // --- views --------------------------------------------------------------------------

    const ready = () => Boolean(prefs.provider && PROVIDERS[prefs.provider] && prefs.models[prefs.provider] && getKey(prefs.provider));

    function show(view) {
        ['loading', 'unavailable', 'setup', 'chat'].forEach((id) => { $(id).hidden = id !== view; });
        $('new-chat').hidden = view !== 'chat';
        $('open-settings').hidden = view !== 'chat';
    }

    function showChat() {
        $('model-name').textContent = `Model: ${prefs.models[prefs.provider]}.`;
        renderConversation();
        show('chat');
        $('input').focus();
    }

    function showUnavailable(message) {
        $('unavailable').textContent = message;
        show('unavailable');
    }

    // --- the wiki page around the chat --------------------------------------------------

    window.addEventListener('message', (event) => {
        if (!config || event.source !== window.parent || !config.embed_origins.includes(event.origin)) return;
        const data = event.data;
        if (!data || data.type !== 'vdw-chat-page') return;
        parentOrigin = event.origin;
        page = {title: String(data.title || ''), url: String(data.url || ''), slug: String(data.slug || '')};
        if (!$('chat').hidden && !history.length && !running) renderConversation();  // adds "Summarize this page"
    });

    // --- start --------------------------------------------------------------------------

    $('setup').addEventListener('submit', saveSetup);
    document.querySelectorAll('input[name="provider"]').forEach((radio) => radio.addEventListener('change', showProvider));
    $('key').addEventListener('input', () => { $('model-row').hidden = true; });
    $('cancel-setup').addEventListener('click', showChat);
    $('forget').addEventListener('click', () => {
        forgetKey(selectedProvider());
        showProvider();
        $('cancel-setup').hidden = !ready();
    });
    $('open-settings').addEventListener('click', showSetup);
    $('new-chat').addEventListener('click', () => {
        history = [];
        saveHistory();
        renderConversation();
        $('input').focus();
    });
    $('close').addEventListener('click', () => window.parent.postMessage({type: 'vdw-chat-close'}, parentOrigin || '*'));
    $('composer').addEventListener('submit', (event) => {
        event.preventDefault();
        if (running) running.abort(); else ask($('input').value);
    });
    $('input').addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            if (!running) ask($('input').value);
        }
    });

    fetch('config.json').then((response) => {
        if (!response.ok) throw new Error('config');
        return response.json();
    }).then((loaded) => {
        config = loaded;
        if (!config.enabled) { showUnavailable('The AI chat is switched off at the moment.'); return; }
        $('close').hidden = !embedded;
        if (ready()) showChat(); else showSetup();
        // Ask the wiki page which page this is. The message holds nothing private; the answer is
        // only accepted from the wiki's own addresses (see the listener above).
        if (embedded) window.parent.postMessage({type: 'vdw-chat-ready'}, '*');
    }).catch(() => showUnavailable('The AI chat could not be loaded. Please try again later.'));
})();
