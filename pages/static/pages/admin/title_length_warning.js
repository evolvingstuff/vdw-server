(function () {
    var LENGTH_LIMIT = 60;

    function formatMessage(length) {
        return 'Google typically truncates titles longer than ' + LENGTH_LIMIT + ' characters. ' +
            'Current length: ' + length + '. Consider trimming this title so search results show the full text.';
    }

    function ensureWarningElement(field) {
        if (field._vdwTitleWarning) {
            return field._vdwTitleWarning;
        }

        var warning = document.createElement('p');
        warning.className = 'vdw-title-length-warning';
        warning.setAttribute('role', 'status');
        warning.setAttribute('aria-live', 'polite');
        warning.style.display = 'none';
        warning.style.margin = '6px 0 0 0';
        warning.style.fontSize = '12px';
        warning.style.color = '#ba2121';

        field.insertAdjacentElement('afterend', warning);
        field._vdwTitleWarning = warning;
        return warning;
    }

    function updateWarning(field) {
        var warning = ensureWarningElement(field);
        var length = (field.value || '').length;

        if (length > LENGTH_LIMIT) {
            warning.textContent = formatMessage(length);
            warning.style.display = '';
        } else {
            warning.textContent = '';
            warning.style.display = 'none';
        }
    }

    function init() {
        if (!document.body || !document.body.classList.contains('change-form')) {
            return;
        }

        var titleField = document.getElementById('id_title');
        if (!titleField) {
            return;
        }

        var handler = function () {
            updateWarning(titleField);
        };

        titleField.addEventListener('input', handler);
        titleField.addEventListener('blur', handler);
        updateWarning(titleField);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
