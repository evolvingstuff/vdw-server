# VitaminDWiki: Search Engine Improvements

*September 2026*

This is a plain-language summary of the changes being made to help VitaminDWiki show up better in Google, and what we need from you.

## The short version

- We fixed several technical problems that were quietly working against the site in Google. None of them change what the pages say or how they look to readers.
- We're also reviewing page titles that may claim more than the underlying study shows, and suggesting more careful wording. **Nothing will be changed without your approval.**
- Expect gradual, modest improvement over weeks to months, not an overnight jump. We'll measure the results in Google Search Console.

## Fixes ready to go live

These are all behind the scenes. Readers won't notice any difference.

### 1. Google was seeing two copies of the whole site
The site answered at both `vitamindwiki.com` and `www.vitamindwiki.com` as if they were separate websites. Google could treat this as ~14,800 duplicate pages and split the site's credit between the two copies.
**Fix:** `www.vitamindwiki.com` now forwards to `vitamindwiki.com`. Old links and bookmarks to either address still work.

### 2. Telling Google which address is the "real" one
The same page can be reached through slightly different web addresses, for example with tracking codes added to the end. Each page now carries a small hidden label ("canonical tag") telling Google which address is the official one, so credit isn't split.

### 3. Page summaries for search results
Under each result, Google shows a short summary. The site gave Google no summaries at all, so Google often showed the start of the study citation, such as *"J Womens Health 2012 Oct;21(10):1066-73. doi: 10.1089…"*, which doesn't make anyone want to click.
**Fix:** each page now gives Google a summary taken from its first real paragraph, skipping citations and author lists. About 4 out of 5 pages get one automatically. If you fill in the **Meta description** box when editing a page, your wording is used instead.

### 4. Instructions for search engines
Search engines look for a standard file called `robots.txt` that tells them where the site map is and which areas to skip (such as the admin pages). The site didn't have one; now it does.

### 5. A better homepage title
The homepage showed up in Google as **"Home - VitaminDWiki"**. It now reads **"VitaminDWiki – Vitamin D research, studies and health effects"**. Happy to change the wording if you'd prefer something else.

## In progress: more careful page titles

Google is especially strict with health websites. Titles that state a finding more strongly than the evidence supports can hurt the site's credibility with readers and with Google. Examples:

| Current title | Suggested |
|---|---|
| Red meat consumption **increases risk** of Diabetes | Red meat consumption **linked to higher risk** of Diabetes |
| Chronic lymphocytic leukemia **slowed by** vitamin D – Mayo | Chronic lymphocytic leukemia **slower with higher** vitamin D – Mayo |
| Breast Cancer **prevented by** Vitamin D (most studies concur) | Breast Cancer **less likely with** Vitamin D (most studies concur) |

How it works:
- An AI reads each flagged page and suggests the **smallest possible change**, keeping your wording, style and numbers.
- Titles based on randomized controlled trials are generally left alone, since those studies do show cause and effect.
- Your usual style, such as *"30 percent less likely if…"* and *"2.4X fewer…"*, is considered fine and is not changed.
- In testing, about **1 in 10 titles** got a suggested change. The rest were left as they are.
- **You'll get the full list of suggestions to review.** Only titles you approve will be changed.
- When a title changes, its web address will also be updated, and the old address will automatically forward to the new one, so no links or bookmarks break.

## What we need from you

1. **Homepage summary:** the homepage's summary for Google currently just says "Welcome to VitaminDWiki". Please replace it with one or two sentences describing the site (Admin → Site pages → Home → Meta description).
2. **About / author information:** Google looks for who is behind a health website and their background. The site doesn't currently say. Let's talk about what you'd be comfortable showing, such as your name, background and why you started the site.
3. **Review the suggested title changes** when the list is ready.

## Later, worth considering

- **Very short pages:** about 300 pages have only a sentence or two of text. Short pages can drag down how Google rates the whole site. Some might be merged into related pages or expanded.
- **Image descriptions:** most images are labelled simply "image". Real descriptions help Google Images and visually impaired readers.
- **Homepage speed:** the homepage loads more slowly than the other pages and could be sped up.

## What to expect

- Google has to revisit the pages before these changes count. That usually takes a few weeks.
- These changes remove things that were holding the site back. They're not a guarantee of higher rankings, since Google weighs many factors, including links from other sites and how it views the content overall.
- We'll compare the Google Search Console numbers (visits from Google, how often the site appears, average position) from before and after the changes.
