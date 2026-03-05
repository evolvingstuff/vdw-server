(function(globalObject, factory) {
    var api = factory();

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }

    if (globalObject) {
        globalObject.VDWLegacyBoxRendering = api;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
    var TABLE_ROW_RE = /^\|.*\|\s*$/;
    var TABLE_DIVIDER_RE = /^\|\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$/;
    var LIST_LINE_RE = /^(?:[*+-]|\d+\.)\s+/;
    var INLINE_WRAPPER_ONLY_RE = /^<\/?(?:strong|em|i|b|span)(?:\s[^>]*)?>$/i;
    var STANDALONE_BLOCK_RE = /^<(?:img|figure|table|ul|ol|blockquote|pre|div|p|h[1-6]|hr)\b/i;

    function normalizeLegacyBoxInnerHtml(rawHtml) {
        if (typeof rawHtml !== 'string' || !looksLikeLegacyBoxContent(rawHtml)) {
            return rawHtml;
        }

        var lines = rawHtml.replace(/\r\n?/g, '\n').split('\n');
        var output = [];
        var index = 0;

        while (index < lines.length) {
            var line = normalizeLine(lines[index]);

            if (!line || isInlineWrapperOnly(line)) {
                index += 1;
                continue;
            }

            if (TABLE_ROW_RE.test(line)) {
                var tableResult = consumeTable(lines, index);
                if (tableResult) {
                    output.push(tableResult.html);
                    index = tableResult.nextIndex;
                    continue;
                }
            }

            if (LIST_LINE_RE.test(line) && !isHeadingArtifactLine(line)) {
                var listResult = consumeList(lines, index);
                if (listResult) {
                    output.push(listResult.html);
                    index = listResult.nextIndex;
                    continue;
                }
            }

            if (isHorizontalRule(line)) {
                output.push('<hr>');
                index += 1;
                continue;
            }

            if (STANDALONE_BLOCK_RE.test(line)) {
                output.push(line);
                index += 1;
                continue;
            }

            output.push('<p class="legacy-box-line">' + sanitizeLegacyInlineHtml(line) + '</p>');
            index += 1;
        }

        return output.join('\n');
    }

    function normalizeLegacyBoxElements(rootElement) {
        var boxes = collectLegacyBoxes(rootElement);

        boxes.forEach(function(box) {
            if (box.dataset.vdwLegacyBoxNormalized === '1') {
                return;
            }

            var original = box.innerHTML;
            var normalized = normalizeLegacyBoxInnerHtml(original);
            if (normalized !== original) {
                box.innerHTML = normalized;
                box.classList.add('legacy-box-rendered');
            }

            box.dataset.vdwLegacyBoxNormalized = '1';
        });
    }

    function collectLegacyBoxes(rootElement) {
        if (typeof document === 'undefined') {
            return [];
        }

        if (!rootElement) {
            return Array.from(document.querySelectorAll('.markdown-content .border, .markdown-content .legacy-box'));
        }

        if (rootElement.matches && rootElement.matches('.border, .legacy-box')) {
            return [rootElement].concat(Array.from(rootElement.querySelectorAll('.border, .legacy-box')));
        }

        return Array.from(rootElement.querySelectorAll('.border, .legacy-box')).filter(function(box) {
            return Boolean(box.closest('.markdown-content'));
        });
    }

    function looksLikeLegacyBoxContent(rawHtml) {
        if (typeof rawHtml !== 'string' || rawHtml.indexOf('\n') === -1) {
            return false;
        }

        return (
            /(^|\n)\s*\|.*\|\s*(?=\n|$)/.test(rawHtml) ||
            /(^|\n)\s*(?:[*+-]|\d+\.)\s+(?:<a\b|[^<\n])/.test(rawHtml) ||
            /(^|\n)\s*<a\b[^>]*>/.test(rawHtml) ||
            /(^|\n)\s*---\s*(?:\n|$)/.test(rawHtml) ||
            /(^|\n)\s*\d+\.\s*#\s+/i.test(rawHtml)
        );
    }

    function consumeTable(lines, startIndex) {
        var rows = [];
        var index = startIndex;

        while (index < lines.length) {
            var line = normalizeLine(lines[index]);
            if (!line) {
                index += 1;
                continue;
            }
            if (isInlineWrapperOnly(line)) {
                index += 1;
                continue;
            }
            if (!TABLE_ROW_RE.test(line)) {
                break;
            }

            if (!TABLE_DIVIDER_RE.test(line)) {
                var cells = splitTableRow(line).map(cleanTableCell);
                if (cells.some(function(cell) { return stripHtml(cell) !== ''; })) {
                    rows.push(cells);
                }
            }

            index += 1;
        }

        if (rows.length === 0) {
            return null;
        }

        var columnCount = rows.reduce(function(currentMax, row) {
            return Math.max(currentMax, row.length);
        }, 0);

        var tableRows = rows.map(function(row) {
            var paddedRow = row.slice();
            while (paddedRow.length < columnCount) {
                paddedRow.push('');
            }
            return '<tr>' + paddedRow.map(function(cell) {
                return '<td>' + cell + '</td>';
            }).join('') + '</tr>';
        }).join('');

        return {
            html: '<table class="legacy-box-table"><tbody>' + tableRows + '</tbody></table>',
            nextIndex: index,
        };
    }

    function splitTableRow(line) {
        var trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
        return trimmed.split('|');
    }

    function cleanTableCell(cellHtml) {
        return sanitizeLegacyInlineHtml(cellHtml)
            .replace(/^(?:\*\s+)+/, '')
            .trim();
    }

    function consumeList(lines, startIndex) {
        var items = [];
        var ordered = true;
        var index = startIndex;

        while (index < lines.length) {
            var line = normalizeLine(lines[index]);
            if (!line) {
                index += 1;
                continue;
            }
            if (isInlineWrapperOnly(line)) {
                index += 1;
                continue;
            }
            if (!LIST_LINE_RE.test(line)) {
                break;
            }

            if (!/^\d+\.\s+/.test(line)) {
                ordered = false;
            }

            var itemHtml = sanitizeLegacyInlineHtml(line.replace(LIST_LINE_RE, ''));
            if (itemHtml) {
                items.push(itemHtml);
            }

            index += 1;
        }

        if (items.length === 0) {
            return null;
        }

        var tagName = ordered ? 'ol' : 'ul';
        return {
            html: '<' + tagName + ' class="legacy-box-list">' +
                items.map(function(item) { return '<li>' + item + '</li>'; }).join('') +
                '</' + tagName + '>',
            nextIndex: index,
        };
    }

    function sanitizeLegacyInlineHtml(value) {
        return normalizeLine(value)
            .replace(/^\d+\.\s*#\s*/i, '')
            .replace(/^#\s+/i, '')
            .replace(/^&nbsp;/i, '')
            .trim();
    }

    function normalizeLine(value) {
        return String(value || '').replace(/\u00a0/g, ' ').trim();
    }

    function isInlineWrapperOnly(line) {
        return INLINE_WRAPPER_ONLY_RE.test(line);
    }

    function isHorizontalRule(line) {
        return /^-{3,}$/.test(line) || /^--$/.test(line);
    }

    function isHeadingArtifactLine(line) {
        return /^\d+\.\s*#\s+/i.test(line);
    }

    function stripHtml(value) {
        return String(value || '')
            .replace(/<[^>]+>/g, ' ')
            .replace(/&nbsp;/gi, ' ')
            .trim();
    }

    return {
        looksLikeLegacyBoxContent: looksLikeLegacyBoxContent,
        normalizeLegacyBoxElements: normalizeLegacyBoxElements,
        normalizeLegacyBoxInnerHtml: normalizeLegacyBoxInnerHtml,
    };
});
