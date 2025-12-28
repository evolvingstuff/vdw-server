(function () {
    function fallbackCopy(text) {
        return new Promise(function (resolve, reject) {
            var textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.setAttribute('readonly', '');
            textarea.style.position = 'fixed';
            textarea.style.top = '-1000px';
            textarea.style.left = '-1000px';

            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();

            try {
                var successful = document.execCommand('copy');
                if (!successful) {
                    throw new Error('execCommand returned false');
                }
                resolve();
            } catch (err) {
                reject(err);
            } finally {
                document.body.removeChild(textarea);
            }
        });
    }

    function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text).catch(function () {
                return fallbackCopy(text);
            });
        }
        return fallbackCopy(text);
    }

    function copyHtml(html, plainText) {
        var plain = plainText || '';
        if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem && window.Blob) {
            var payload = {
                'text/html': new Blob([html], { type: 'text/html' }),
            };
            if (plain) {
                payload['text/plain'] = new Blob([plain], { type: 'text/plain' });
            }

            return navigator.clipboard.write([new ClipboardItem(payload)]).catch(function () {
                return copyText(plain || html);
            });
        }

        return copyText(plain || html);
    }

    function setButtonState(button, copied) {
        if (!button) {
            return;
        }

        var defaultLabel = button.dataset.copyLabel || button.textContent;
        var successLabel = button.dataset.copySuccess || 'Copied!';
        var errorLabel = button.dataset.copyError || 'Copy failed';

        if (!button.dataset.copyOriginalLabel) {
            button.dataset.copyOriginalLabel = defaultLabel;
        }

        button.textContent = copied ? successLabel : errorLabel;
        button.classList.add(copied ? 'vdw-copy-success' : 'vdw-copy-error');
        button.disabled = true;

        window.setTimeout(function () {
            button.textContent = button.dataset.copyLabel || button.dataset.copyOriginalLabel;
            button.classList.remove('vdw-copy-success');
            button.classList.remove('vdw-copy-error');
            button.disabled = false;
        }, 1500);
    }

    function handleCopyClick(event) {
        var trigger = event.target.closest('[data-copy-markdown], [data-copy-html]');
        if (!trigger) {
            return;
        }

        event.preventDefault();

        var markdown = trigger.getAttribute('data-copy-markdown');
        if (markdown) {
            copyText(markdown)
                .then(function () {
                    setButtonState(trigger, true);
                })
                .catch(function () {
                    setButtonState(trigger, false);
                });
            return;
        }

        var html = trigger.getAttribute('data-copy-html');
        if (!html) {
            setButtonState(trigger, false);
            return;
        }

        var plain = trigger.getAttribute('data-copy-plain') || '';
        copyHtml(html, plain)
            .then(function () {
                setButtonState(trigger, true);
            })
            .catch(function () {
                setButtonState(trigger, false);
            });
    }

    function init() {
        document.addEventListener('click', handleCopyClick);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
