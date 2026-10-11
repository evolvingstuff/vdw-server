# VitaminDWiki: Search Engine Improvements

*September 2026*

A plain-language summary of changes made to help VitaminDWiki do better in Google.

## Summary

- Fixed a set of technical problems that were quietly working against the site in Google
- None of these changes affect what the pages say, and readers won't notice any difference
- Expect gradual, modest improvement over weeks to months, not an overnight jump
- Results will be tracked in Google Search Console

## Done and live on the site

- **One address for the whole site**
  - The site worked at both `vitamindwiki.com` and `www.vitamindwiki.com`, so Google could see two copies of every page and split the credit between them
  - `www.` now forwards to `vitamindwiki.com`, and old links and bookmarks still work

- **Telling Google which address is the "real" one**
  - The same page can be reached through slightly different addresses (for example, with tracking codes added to the end)
  - Each page now carries a hidden label pointing Google to the official address

- **Summaries under each Google result**
  - The site gave Google no summaries, so Google often showed the start of the study citation (*"J Womens Health 2012 Oct;21(10):1066-73. doi…"*)
  - Pages now supply a summary taken from their first real paragraph, skipping citations, author lists and "Methods" sections
  - About 4 out of 5 pages get one automatically
  - Filling in the **Meta description** box when editing a page overrides it

- **Instructions for search engines**
  - Added the standard `robots.txt` file, which points search engines to the site map and tells them to skip admin pages

- **Better homepage title**
  - Was **"Home - VitaminDWiki"**
  - Now **"VitaminDWiki – Vitamin D research, studies and health effects"**

- **Shorter page titles in Google**
  - Removed " - VitaminDWiki" from the end of article titles, so less of the actual title gets cut off in search results

- **Topic pages added to the site map**
  - The ~500 tag pages (such as *Obesity*, 443 pages) were not in the site map that Google reads
  - They're now included, each with its own summary

- **Cleaner page structure**
  - About 1,800 pages had several "main headings" (Google expects one per page, normally the title)
  - Headings inside pages are now marked as sub-headings, and they look exactly the same as before

- **Faster page loading**
  - Images further down a page now load only as the reader scrolls to them
  - The site's style files are cached by browsers instead of being downloaded on every visit

- **No more endless duplicate list pages**
  - Addresses such as "page 99999 of All Pages" used to show a copy of the last page
  - They now correctly report "not found"

## In progress

- **More careful page titles**
  - Google is especially strict with health websites, and titles that state a finding more strongly than the study shows can hurt the site's credibility
  - An AI flags such titles and suggests the smallest possible change, keeping the original wording, style and numbers, for example:
    - *Red meat consumption **increases risk** of Diabetes* → *Red meat consumption **linked to higher risk** of Diabetes*
    - *Breast Cancer **prevented by** Vitamin D (most studies concur)* → *Breast Cancer **less likely with** Vitamin D (most studies concur)*
  - Titles based on randomized controlled trials are generally left alone, since those studies do show cause and effect
  - House style such as *"30 percent less likely if…"* and *"2.4X fewer…"* is considered fine and is not changed
  - In testing, about 1 in 10 titles got a suggestion
  - Every suggestion will be reviewed before anything changes
  - Old page addresses will forward to the new ones, so no links or bookmarks break

- **Fixing broken links between pages**
  - About 2,200 links inside pages lead to pages that no longer exist, mostly left over from the move off the old wiki software
    - ~700 are old wiki "search" links
    - ~400 are old file-download links
    - ~1,100 point to pages that were renamed or never moved over
  - Plan: fix the obvious ones automatically, review the rest, and point old addresses to their new pages

## Later, worth considering

- **Author / About information**: Google looks for who is behind a health website and their background
- **Very short pages**: about 300 pages have only a sentence or two, which can drag down how Google rates the whole site
- **Image descriptions**: most images are labelled simply "image"; real descriptions help Google Images and visually impaired readers
- **Page summaries written by hand or by AI**: better than the automatic ones, for the most important pages

## What to expect

- Google has to revisit the pages before changes count, which usually takes a few weeks
- These changes remove things that were holding the site back, but don't guarantee higher rankings
- Google also weighs links from other sites and how it views the content overall
- Before-and-after numbers from Google Search Console (visits, how often the site appears, average position) will show the real effect
