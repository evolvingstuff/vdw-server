/*
 * Make Django's filter_horizontal search boxes (the tag pickers) match the START of words.
 * Every typed word must begin some word of the option, in any order:
 * "iu" finds "IU" but not "Magnesium"; "tub rec" finds "Tuberculosis + Vitamin D Receptor".
 */
(function () {
    'use strict';

    function words(text) {
        return text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
    }

    function install() {
        if (!window.SelectBox || window.SelectBox._wordStartFilter) {
            return;
        }
        window.SelectBox.filter = function (id, text) {
            const tokens = words(text);
            for (const node of window.SelectBox.cache[id]) {
                const nodeWords = words(node.text);
                node.displayed = tokens.every(token => nodeWords.some(word => word.startsWith(token))) ? 1 : 0;
            }
            window.SelectBox.redisplay(id);
        };
        window.SelectBox._wordStartFilter = true;
    }

    install();
    window.addEventListener('load', install);
})();
