(function () {
    var AUTO_SAVE_INTERVAL_MS = 5000;
    var SAVE_THROTTLE_MS = 800;
    var DRAFT_KEY_PREFIX = 'vdw_admin_draft:';

    function parseAdminChangePath(pathname) {
        var changeMatch = pathname.match(/\/admin\/([^/]+)\/([^/]+)\/([^/]+)\/change\/$/);
        if (changeMatch) {
            return {
                app: changeMatch[1],
                model: changeMatch[2],
                objectId: changeMatch[3],
                view: 'change',
            };
        }

        var addMatch = pathname.match(/\/admin\/([^/]+)\/([^/]+)\/add\/$/);
        if (addMatch) {
            return {
                app: addMatch[1],
                model: addMatch[2],
                objectId: 'add',
                view: 'add',
            };
        }

        return null;
    }

    function buildDraftKey(adminInfo) {
        return DRAFT_KEY_PREFIX + adminInfo.app + '.' + adminInfo.model + ':' + adminInfo.objectId;
    }

    function findChangeForm() {
        if (!document.body || !document.body.classList.contains('change-form')) {
            return null;
        }

        return document.querySelector('form[id$="_form"]');
    }

    function shouldSkipField(field) {
        if (!field || !field.name) {
            return true;
        }

        if (field.disabled) {
            return true;
        }

        var tagName = (field.tagName || '').toLowerCase();
        if (tagName === 'button') {
            return true;
        }

        var type = (field.type || '').toLowerCase();
        if (type === 'submit' || type === 'button' || type === 'reset' || type === 'file') {
            return true;
        }

        if (type === 'hidden' && field.name === 'csrfmiddlewaretoken') {
            return true;
        }

        return false;
    }

    function collectFormValues(form) {
        var fields = form.querySelectorAll('input, textarea, select');
        var values = {};

        for (var i = 0; i < fields.length; i++) {
            var field = fields[i];
            if (shouldSkipField(field)) {
                continue;
            }

            var type = (field.type || '').toLowerCase();

            if (type === 'checkbox') {
                values[field.name] = Boolean(field.checked);
                continue;
            }

            if (type === 'radio') {
                if (field.checked) {
                    values[field.name] = field.value;
                }
                continue;
            }

            if (field.tagName && field.tagName.toLowerCase() === 'select' && field.multiple) {
                var selected = [];
                for (var j = 0; j < field.options.length; j++) {
                    var option = field.options[j];
                    if (option.selected) {
                        selected.push(option.value);
                    }
                }
                values[field.name] = selected;
                continue;
            }

            values[field.name] = field.value;
        }

        return values;
    }

    function applyFormValues(form, values) {
        var fields = form.querySelectorAll('input, textarea, select');

        for (var i = 0; i < fields.length; i++) {
            var field = fields[i];
            if (shouldSkipField(field)) {
                continue;
            }

            if (!Object.prototype.hasOwnProperty.call(values, field.name)) {
                continue;
            }

            var storedValue = values[field.name];
            var type = (field.type || '').toLowerCase();

            if (type === 'checkbox') {
                field.checked = Boolean(storedValue);
                continue;
            }

            if (type === 'radio') {
                field.checked = field.value === storedValue;
                continue;
            }

            if (field.tagName && field.tagName.toLowerCase() === 'select' && field.multiple) {
                var selectedValues = Array.isArray(storedValue) ? storedValue : [];
                for (var j = 0; j < field.options.length; j++) {
                    var option = field.options[j];
                    option.selected = selectedValues.indexOf(option.value) !== -1;
                }
                continue;
            }

            field.value = storedValue;
        }
    }

    function safeJsonParse(raw) {
        try {
            return JSON.parse(raw);
        } catch (err) {
            return null;
        }
    }

    function readDraft(key) {
        try {
            var raw = window.localStorage.getItem(key);
            if (!raw) {
                return null;
            }
            return safeJsonParse(raw);
        } catch (err) {
            return null;
        }
    }

    function writeDraft(key, draft) {
        try {
            window.localStorage.setItem(key, JSON.stringify(draft));
            return true;
        } catch (err) {
            return false;
        }
    }

    function clearDraft(key) {
        try {
            window.localStorage.removeItem(key);
        } catch (err) {
            // no-op
        }
    }

    function getSuccessMessagesText() {
        var nodes = document.querySelectorAll('.messagelist li.success');
        if (!nodes || nodes.length === 0) {
            return '';
        }

        var combined = '';
        for (var i = 0; i < nodes.length; i++) {
            combined += (nodes[i].textContent || '') + ' ';
        }

        return combined.toLowerCase();
    }

    function maybeClearDraftAfterSave(adminInfo, draftKey) {
        var text = getSuccessMessagesText();
        if (!text) {
            return;
        }

        if (text.indexOf('added successfully') !== -1) {
            clearDraft(draftKey);
            clearDraft(DRAFT_KEY_PREFIX + adminInfo.app + '.' + adminInfo.model + ':add');
            return;
        }

        if (text.indexOf('changed successfully') !== -1) {
            clearDraft(draftKey);
        }
    }

    function formatTimestamp(isoString) {
        if (!isoString) {
            return null;
        }

        var date = new Date(isoString);
        if (isNaN(date.getTime())) {
            return null;
        }

        return date.toLocaleString();
    }

    function renderDraftBanner(draft, onRestore, onDiscard) {
        var content = document.getElementById('content');
        if (!content) {
            return null;
        }

        var banner = document.createElement('div');
        banner.className = 'vdw-draft-banner';
        banner.style.marginBottom = '12px';

        var messageList = document.createElement('ul');
        messageList.className = 'messagelist';
        messageList.style.marginBottom = '0';

        var item = document.createElement('li');
        item.className = 'info';
        item.style.display = 'flex';
        item.style.alignItems = 'center';
        item.style.justifyContent = 'space-between';
        item.style.gap = '12px';

        var left = document.createElement('span');
        var when = formatTimestamp(draft.updatedAt) || 'unknown time';
        left.textContent = 'Unsaved draft found (last saved ' + when + ').';

        var right = document.createElement('span');
        right.style.display = 'flex';
        right.style.gap = '8px';

        var restore = document.createElement('button');
        restore.type = 'button';
        restore.textContent = 'Restore';
        restore.className = 'button';

        var discard = document.createElement('button');
        discard.type = 'button';
        discard.textContent = 'Discard';

        restore.addEventListener('click', function () {
            onRestore();
            banner.remove();
        });

        discard.addEventListener('click', function () {
            onDiscard();
            banner.remove();
        });

        right.appendChild(restore);
        right.appendChild(discard);

        item.appendChild(left);
        item.appendChild(right);
        messageList.appendChild(item);
        banner.appendChild(messageList);

        content.insertBefore(banner, content.firstChild);
        return banner;
    }

    function init() {
        var adminInfo = parseAdminChangePath(window.location.pathname);
        if (!adminInfo) {
            return;
        }

        var form = findChangeForm();
        if (!form) {
            return;
        }

        var draftKey = buildDraftKey(adminInfo);
        maybeClearDraftAfterSave(adminInfo, draftKey);

        var initialValues = collectFormValues(form);
        var initialSerialized = JSON.stringify(initialValues);

        var isSubmitting = false;
        var isDirty = false;

        function updateDirtyState() {
            var currentSerialized = JSON.stringify(collectFormValues(form));
            isDirty = currentSerialized !== initialSerialized;
        }

        function saveDraft() {
            updateDirtyState();
            if (!isDirty) {
                return;
            }

            var draft = {
                version: 1,
                updatedAt: new Date().toISOString(),
                values: collectFormValues(form),
            };

            writeDraft(draftKey, draft);
        }

        var saveTimer = null;
        function scheduleSaveDraft() {
            if (saveTimer) {
                window.clearTimeout(saveTimer);
            }
            saveTimer = window.setTimeout(saveDraft, SAVE_THROTTLE_MS);
        }

        var existingDraft = readDraft(draftKey);
        if (existingDraft && existingDraft.values) {
            var draftSerialized = JSON.stringify(existingDraft.values);
            if (draftSerialized !== initialSerialized) {
                renderDraftBanner(
                    existingDraft,
                    function () {
                        applyFormValues(form, existingDraft.values);
                        updateDirtyState();
                        scheduleSaveDraft();
                    },
                    function () {
                        clearDraft(draftKey);
                    }
                );
            }
        }

        form.addEventListener('submit', function () {
            isSubmitting = true;
        });

        form.addEventListener(
            'input',
            function () {
                scheduleSaveDraft();
            },
            true
        );

        form.addEventListener(
            'change',
            function () {
                scheduleSaveDraft();
            },
            true
        );

        window.addEventListener('beforeunload', function (event) {
            updateDirtyState();
            if (!isDirty || isSubmitting) {
                return;
            }

            event.preventDefault();
            event.returnValue = '';
        });

        document.addEventListener('click', function (event) {
            var anchor = event.target && event.target.closest ? event.target.closest('a') : null;
            if (!anchor || !anchor.href) {
                return;
            }

            if (anchor.getAttribute('href') === '#' || anchor.getAttribute('href') === '') {
                return;
            }

            updateDirtyState();
            if (!isDirty || isSubmitting) {
                return;
            }

            var shouldLeave = window.confirm('You have unsaved changes. Leave this page?');
            if (!shouldLeave) {
                event.preventDefault();
                event.stopPropagation();
            }
        });

        window.setInterval(saveDraft, AUTO_SAVE_INTERVAL_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

