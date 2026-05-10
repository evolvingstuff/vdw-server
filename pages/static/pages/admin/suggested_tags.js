(function () {
    "use strict";

    function slugify(value) {
        return value
            .normalize("NFKD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "");
    }

    function markdownHeadingTexts(markdownSource) {
        const headings = [];
        const markdownHeadingRe = /^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/gm;
        let match = markdownHeadingRe.exec(markdownSource);
        while (match) {
            const heading = match[1].trim();
            if (heading) {
                headings.push(heading);
            }
            match = markdownHeadingRe.exec(markdownSource);
        }

        const htmlHeadingRe = /<h[1-6][^>]*>(.*?)<\/h[1-6]>/gis;
        match = htmlHeadingRe.exec(markdownSource);
        while (match) {
            const heading = match[1].replace(/<[^>]+>/g, "").trim();
            if (heading) {
                headings.push(heading);
            }
            match = htmlHeadingRe.exec(markdownSource);
        }
        return headings;
    }

    function suggestionHaystack(title, markdownSource) {
        return [title].concat(markdownHeadingTexts(markdownSource))
            .map(slugify)
            .filter(Boolean)
            .map(function (phraseSlug) {
                return "-" + phraseSlug + "-";
            })
            .join("");
    }

    function refreshFilterHorizontal(fieldId) {
        if (!window.SelectFilter) {
            return;
        }
        window.SelectFilter.refresh_icons(fieldId);
        window.SelectFilter.refresh_filtered_selects(fieldId);
        window.SelectFilter.refresh_filtered_warning(fieldId);
    }

    function findOptionByValue(selectBox, value) {
        for (const option of selectBox.options) {
            if (option.value === value) {
                return option;
            }
        }
        return null;
    }

    function selectedTagIds(fieldId) {
        const selectedIds = new Set();
        const originalBox = document.getElementById(fieldId);
        if (originalBox) {
            for (const option of originalBox.options) {
                if (option.selected) {
                    selectedIds.add(option.value);
                }
            }
        }

        const selectedBox = document.getElementById(fieldId + "_to");
        if (!selectedBox) {
            return selectedIds;
        }

        for (const option of selectedBox.options) {
            selectedIds.add(option.value);
        }
        return selectedIds;
    }

    function currentSuggestions(wrapper, allTags) {
        const titleInput = document.getElementById("id_title");
        const contentInput = document.getElementById("id_content_md");
        const fieldId = wrapper.dataset.targetSelectId;
        const assignedTagIds = selectedTagIds(fieldId);
        const haystack = suggestionHaystack(
            titleInput ? titleInput.value : "",
            contentInput ? contentInput.value : ""
        );

        if (!haystack) {
            return [];
        }

        return allTags.filter(function (tag) {
            const tagId = String(tag.id);
            return !assignedTagIds.has(tagId) && tag.slug && haystack.includes("-" + tag.slug);
        });
    }

    function renderSuggestions(wrapper, allTags) {
        const chips = wrapper.querySelector(".vdw-suggested-tags__chips");
        const empty = wrapper.querySelector(".vdw-suggested-tags__empty");
        const suggestions = currentSuggestions(wrapper, allTags);

        chips.innerHTML = "";
        for (const tag of suggestions) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "button vdw-suggested-tags__chip";
            button.dataset.tagId = String(tag.id);
            button.title = "Add suggested tag: " + tag.name;
            button.textContent = "+ " + tag.name;
            chips.appendChild(button);
        }

        empty.hidden = suggestions.length > 0;
    }

    function renderAllSuggestions(allTags) {
        for (const wrapper of document.querySelectorAll(".vdw-suggested-tags")) {
            renderSuggestions(wrapper, allTags);
        }
    }

    function scheduleRender(allTags) {
        window.setTimeout(function () {
            renderAllSuggestions(allTags);
        }, 0);
    }

    function observeChosenTags(allTags) {
        for (const wrapper of document.querySelectorAll(".vdw-suggested-tags")) {
            const fieldId = wrapper.dataset.targetSelectId;
            const selectedBox = document.getElementById(fieldId + "_to");
            if (!selectedBox) {
                continue;
            }

            const observer = new MutationObserver(function () {
                renderSuggestions(wrapper, allTags);
            });
            observer.observe(selectedBox, {childList: true});
        }
    }

    function addSuggestedTag(button, allTags) {
        const wrapper = button.closest(".vdw-suggested-tags");
        const tagId = button.dataset.tagId;
        const fieldId = wrapper.dataset.targetSelectId;
        const fromBox = document.getElementById(fieldId + "_from");
        const toBox = document.getElementById(fieldId + "_to");

        if (!tagId || !fieldId || !fromBox || !toBox) {
            throw new Error("Suggested tag controls are not connected to the tags selector.");
        }

        if (findOptionByValue(toBox, tagId)) {
            renderSuggestions(wrapper, allTags);
            return;
        }

        if (!window.SelectBox) {
            throw new Error("Django admin SelectBox is not loaded.");
        }

        const availableFilter = document.getElementById(fieldId + "_input");
        if (availableFilter) {
            availableFilter.value = "";
            window.SelectBox.filter(fieldId + "_from", "");
        }

        const option = findOptionByValue(fromBox, tagId);
        if (!option) {
            throw new Error("Suggested tag is missing from the available tags selector.");
        }

        for (const fromOption of fromBox.options) {
            fromOption.selected = false;
        }
        option.selected = true;

        window.SelectBox.move(fieldId + "_from", fieldId + "_to");
        refreshFilterHorizontal(fieldId);
        renderSuggestions(wrapper, allTags);
    }

    document.addEventListener("DOMContentLoaded", function () {
        const dataElement = document.getElementById("vdw-suggested-tags-data");
        if (!dataElement) {
            return;
        }

        const allTags = JSON.parse(dataElement.textContent);
        renderAllSuggestions(allTags);
        scheduleRender(allTags);
        window.addEventListener("load", function () {
            renderAllSuggestions(allTags);
            observeChosenTags(allTags);
        });

        const titleInput = document.getElementById("id_title");
        const contentInput = document.getElementById("id_content_md");
        if (titleInput) {
            titleInput.addEventListener("input", function () {
                renderAllSuggestions(allTags);
            });
        }
        if (contentInput) {
            contentInput.addEventListener("input", function () {
                renderAllSuggestions(allTags);
            });
        }

        document.addEventListener("click", function (event) {
            const suggestionButton = event.target.closest(".vdw-suggested-tags__chip");
            if (suggestionButton) {
                addSuggestedTag(suggestionButton, allTags);
                return;
            }

            if (event.target.closest(".selector")) {
                scheduleRender(allTags);
            }
        });
    });
}());
