const test = require('node:test');
const assert = require('node:assert/strict');

const {
    looksLikeLegacyBoxContent,
    normalizeLegacyBoxInnerHtml,
} = require('../pages/static/pages/js/legacy_box_rendering.js');

test('converts pipe-table markup inside legacy boxes into a real table', () => {
    const source = [
        '<strong>Some items from the excellent PDF</strong>',
        '<strong>',
        '| | |',
        '| --- | --- |',
        '| Vitamin E competes with Vitamin D  | -15% |',
        '| Gut-friendly forms |   * ? % |',
        '</strong>',
    ].join('\n');

    const normalized = normalizeLegacyBoxInnerHtml(source);

    assert.match(normalized, /<table class="legacy-box-table">/);
    assert.doesNotMatch(normalized, /\| --- \| --- \|/);
    assert.match(normalized, /<td>Vitamin E competes with Vitamin D<\/td>/);
    assert.match(normalized, /<td>\? %<\/td>/);
});

test('restores line structure for legacy text and link bullets inside boxes', () => {
    const source = [
        '1. # <span style="color:#00F;">Does not appear to cover the following bio-availability issues</span>',
        'Water soluble form of Vitamin D appears better for those with poor guts',
        '<a href="/pages/low-fat/">Low-fat milk - 60% less vitamin D gets into the blood</a>',
        '---',
        '* <a href="/pages/state-of-the-art/">Vitamin D bioavailability: State of the art - Oct 2014</a>',
        '* <a href="/pages/review/">Review of bioavailability of vitamin D</a>',
    ].join('\n');

    const normalized = normalizeLegacyBoxInnerHtml(source);

    assert.match(
        normalized,
        /<p class="legacy-box-line"><span style="color:#00F;">Does not appear to cover the following bio-availability issues<\/span><\/p>/
    );
    assert.match(
        normalized,
        /<p class="legacy-box-line">Water soluble form of Vitamin D appears better for those with poor guts<\/p>/
    );
    assert.match(
        normalized,
        /<p class="legacy-box-line"><a href="\/pages\/low-fat\/">Low-fat milk - 60% less vitamin D gets into the blood<\/a><\/p>/
    );
    assert.match(normalized, /<hr>/);
    assert.match(normalized, /<ul class="legacy-box-list">/);
    assert.match(
        normalized,
        /<li><a href="\/pages\/state-of-the-art\/">Vitamin D bioavailability: State of the art - Oct 2014<\/a><\/li>/
    );
});

test('leaves already-rendered box markup alone', () => {
    const source = '<p>Already rendered content</p>';

    assert.equal(looksLikeLegacyBoxContent(source), false);
    assert.equal(normalizeLegacyBoxInnerHtml(source), source);
});
