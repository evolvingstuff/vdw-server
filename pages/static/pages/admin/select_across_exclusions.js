/*global gettext, interpolate, ngettext */
'use strict';
{
    const EXCLUDED_IDS_FIELD_NAME = '_bulk_tag_excluded_page_ids';

    function show(selector) {
        document.querySelectorAll(selector).forEach(function(el) {
            el.classList.remove('hidden');
        });
    }

    function hide(selector) {
        document.querySelectorAll(selector).forEach(function(el) {
            el.classList.add('hidden');
        });
    }

    function showQuestion(options) {
        hide(options.acrossClears);
        show(options.acrossQuestions);
        hide(options.allContainer);
    }

    function showClear(options) {
        show(options.acrossClears);
        hide(options.acrossQuestions);
        const actionContainer = document.querySelector(options.actionContainer);
        if (actionContainer) {
            actionContainer.classList.remove(options.selectedClass);
        }
        show(options.allContainer);
        hide(options.counterContainer);
    }

    function reset(options) {
        hide(options.acrossClears);
        hide(options.acrossQuestions);
        hide(options.allContainer);
        show(options.counterContainer);
    }

    function clearAcross(options) {
        reset(options);
        document.querySelectorAll(options.acrossInput).forEach(function(acrossInput) {
            acrossInput.value = 0;
        });
        const actionContainer = document.querySelector(options.actionContainer);
        if (actionContainer) {
            actionContainer.classList.remove(options.selectedClass);
        }
    }

    function checker(actionCheckboxes, options, checked) {
        if (checked) {
            showQuestion(options);
        } else {
            reset(options);
        }

        actionCheckboxes.forEach(function(el) {
            el.checked = checked;
            const row = el.closest('tr');
            if (row) {
                row.classList.toggle(options.selectedClass, checked);
            }
        });
    }

    function updateCounter(actionCheckboxes, options) {
        const selectedCount = Array.from(actionCheckboxes).filter(function(el) {
            return el.checked;
        }).length;
        const counter = document.querySelector(options.counterContainer);
        if (!counter) {
            return;
        }

        const actionsCount = Number(counter.dataset.actionsIcnt);
        counter.textContent = interpolate(
            ngettext('%(sel)s of %(cnt)s selected', '%(sel)s of %(cnt)s selected', selectedCount),
            {
                sel: selectedCount,
                cnt: actionsCount,
            },
            true,
        );

        const allToggle = document.getElementById(options.allToggleId);
        if (!allToggle) {
            return;
        }

        allToggle.checked = selectedCount === actionCheckboxes.length;
        if (allToggle.checked) {
            showQuestion(options);
        } else {
            clearAcross(options);
        }
    }

    const defaults = {
        actionContainer: 'div.actions',
        counterContainer: 'span.action-counter',
        allContainer: 'div.actions span.all',
        acrossInput: 'div.actions input.select-across',
        acrossQuestions: 'div.actions span.question',
        acrossClears: 'div.actions span.clear',
        allToggleId: 'action-toggle',
        selectedClass: 'selected',
    };

    function getActionForm() {
        return document.getElementById('changelist-form');
    }

    function getExcludedIdsField() {
        const form = getActionForm();
        if (!form) {
            return null;
        }

        let field = form.querySelector('input[name="' + EXCLUDED_IDS_FIELD_NAME + '"]');
        if (!field) {
            field = document.createElement('input');
            field.type = 'hidden';
            field.name = EXCLUDED_IDS_FIELD_NAME;
            form.appendChild(field);
        }
        return field;
    }

    function readExcludedIds() {
        const field = getExcludedIdsField();
        const excludedIds = new Set();
        if (!field || !field.value) {
            return excludedIds;
        }

        field.value.split(',').forEach(function(token) {
            const normalizedToken = token.trim();
            if (normalizedToken) {
                excludedIds.add(normalizedToken);
            }
        });

        return excludedIds;
    }

    function writeExcludedIds(excludedIds) {
        const field = getExcludedIdsField();
        if (!field) {
            return;
        }

        field.value = Array.from(excludedIds).join(',');
    }

    function isAcrossSelected(options) {
        return Array.from(document.querySelectorAll(options.acrossInput)).some(function(acrossInput) {
            return acrossInput.value === '1';
        });
    }

    function updateAcrossSummary(options, excludedIds) {
        document.querySelectorAll(options.allContainer).forEach(function(allContainer) {
            if (!allContainer.dataset.baseText) {
                allContainer.dataset.baseText = allContainer.textContent.trim();
            }

            if (excludedIds.size === 0) {
                allContainer.textContent = allContainer.dataset.baseText;
                return;
            }

            const suffix = excludedIds.size === 1
                ? ' except 1 unchecked row'
                : ' except ' + excludedIds.size + ' unchecked rows';
            allContainer.textContent = allContainer.dataset.baseText + suffix;
        });
    }

    window.Actions = function(actionCheckboxes, options) {
        options = Object.assign({}, defaults, options);

        let listEditableChanged = false;
        let lastChecked = null;
        let shiftPressed = false;
        const excludedIds = readExcludedIds();

        function syncAcrossState() {
            if (!isAcrossSelected(options)) {
                updateAcrossSummary(options, excludedIds);
                return;
            }

            showClear(options);
            updateAcrossSummary(options, excludedIds);

            const actionContainer = document.querySelector(options.actionContainer);
            if (actionContainer) {
                actionContainer.classList.add(options.selectedClass);
            }
        }

        document.addEventListener('keydown', function(event) {
            shiftPressed = event.shiftKey;
        });

        document.addEventListener('keyup', function(event) {
            shiftPressed = event.shiftKey;
        });

        const allToggle = document.getElementById(options.allToggleId);
        if (!allToggle) {
            return;
        }

        allToggle.addEventListener('click', function() {
            if (isAcrossSelected(options)) {
                if (this.checked) {
                    excludedIds.clear();
                    writeExcludedIds(excludedIds);
                    checker(actionCheckboxes, options, true);
                    syncAcrossState();
                    return;
                }

                excludedIds.clear();
                writeExcludedIds(excludedIds);
                clearAcross(options);
                checker(actionCheckboxes, options, false);
                updateCounter(actionCheckboxes, options);
                return;
            }

            if (!this.checked) {
                excludedIds.clear();
                writeExcludedIds(excludedIds);
            }

            checker(actionCheckboxes, options, this.checked);
            updateCounter(actionCheckboxes, options);
        });

        document.querySelectorAll(options.acrossQuestions + ' a').forEach(function(el) {
            el.addEventListener('click', function(event) {
                event.preventDefault();
                document.querySelectorAll(options.acrossInput).forEach(function(acrossInput) {
                    acrossInput.value = 1;
                });
                writeExcludedIds(excludedIds);
                syncAcrossState();
            });
        });

        document.querySelectorAll(options.acrossClears + ' a').forEach(function(el) {
            el.addEventListener('click', function(event) {
                event.preventDefault();
                allToggle.checked = false;
                excludedIds.clear();
                writeExcludedIds(excludedIds);
                clearAcross(options);
                checker(actionCheckboxes, options, false);
                updateCounter(actionCheckboxes, options);
            });
        });

        function affectedCheckboxes(target, withModifier) {
            const multiSelect = lastChecked && withModifier && lastChecked !== target;
            if (!multiSelect) {
                return [target];
            }

            const checkboxes = Array.from(actionCheckboxes);
            const targetIndex = checkboxes.findIndex(function(el) {
                return el === target;
            });
            const lastCheckedIndex = checkboxes.findIndex(function(el) {
                return el === lastChecked;
            });
            const startIndex = Math.min(targetIndex, lastCheckedIndex);
            const endIndex = Math.max(targetIndex, lastCheckedIndex);

            return checkboxes.filter(function(el, index) {
                return startIndex <= index && index <= endIndex;
            });
        }

        const resultList = document.getElementById('result_list');
        if (!resultList) {
            return;
        }

        Array.from(resultList.tBodies).forEach(function(el) {
            el.addEventListener('change', function(event) {
                const target = event.target;
                if (target.classList.contains('action-select')) {
                    const checkboxes = affectedCheckboxes(target, shiftPressed);

                    if (isAcrossSelected(options)) {
                        checkboxes.forEach(function(checkbox) {
                            checkbox.checked = target.checked;

                            const row = checkbox.closest('tr');
                            if (row) {
                                row.classList.toggle(options.selectedClass, target.checked);
                            }

                            if (target.checked) {
                                excludedIds.delete(checkbox.value);
                            } else {
                                excludedIds.add(checkbox.value);
                            }
                        });

                        writeExcludedIds(excludedIds);
                        syncAcrossState();
                    } else {
                        checker(checkboxes, options, target.checked);
                        updateCounter(actionCheckboxes, options);
                    }

                    lastChecked = target;
                } else {
                    listEditableChanged = true;
                }
            });
        });

        const actionButton = document.querySelector('#changelist-form button[name=index]');
        if (actionButton) {
            actionButton.addEventListener('click', function(event) {
                if (!listEditableChanged) {
                    return;
                }

                const confirmed = confirm(
                    gettext('You have unsaved changes on individual editable fields. If you run an action, your unsaved changes will be lost.')
                );
                if (!confirmed) {
                    event.preventDefault();
                }
            });
        }

        const saveButton = document.querySelector('#changelist-form input[name=_save]');
        if (saveButton) {
            saveButton.addEventListener('click', function(event) {
                const actionInput = document.querySelector('[name=action]');
                if (!actionInput || !actionInput.value) {
                    return;
                }

                const text = listEditableChanged
                    ? gettext('You have selected an action, and you haven’t saved your changes to individual fields yet. Please click OK to save. You’ll need to re-run the action.')
                    : gettext('You have selected an action, and you haven’t made any changes on individual fields. You’re probably looking for the Go button rather than the Save button.');
                if (!confirm(text)) {
                    event.preventDefault();
                }
            });
        }

        window.addEventListener('pageshow', function() {
            if (isAcrossSelected(options)) {
                syncAcrossState();
                return;
            }

            updateCounter(actionCheckboxes, options);
        });

        syncAcrossState();
    };
}
