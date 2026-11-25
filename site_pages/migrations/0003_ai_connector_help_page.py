"""Create the "Use VitaminDWiki in ChatGPT or Claude" help page (/use-with-ai/).

Seeded once so it reaches the live site with the connector (/mcp); after that it is an
ordinary site page, edited in the admin. Nothing happens if a page with the slug exists.
"""

from django.conf import settings
from django.db import migrations
from django.utils import timezone

SLUG = 'use-with-ai'
TITLE = 'Use VitaminDWiki in ChatGPT or Claude'
META_DESCRIPTION = (
    'Connect VitaminDWiki to ChatGPT or Claude so your AI assistant answers vitamin D questions '
    'from VitaminDWiki pages, with links.'
)


def content_md() -> str:
    address = f"{settings.SITE_BASE_URL.rstrip('/')}/mcp"
    return f"""## Ask your AI assistant, get answers from VitaminDWiki

If you use **ChatGPT** or **Claude**, you can connect them to VitaminDWiki. Your assistant then searches VitaminDWiki's 14,000+ pages for you and answers with links to the pages it used.

It is free from VitaminDWiki and you don't need an account here. You use your own ChatGPT or Claude account.

---

### The address you will need

Copy this address. Both apps ask for it during setup:

**`{address}`**

---

### Claude

Works on all Claude plans. The free plan allows one connector like this.

1. Open Claude and go to **Customize**, then **Connectors**.
2. Click **+ Add**, then **Add custom connector**.
3. For the name, type **VitaminDWiki**. For the URL, paste the address above.
4. Click **Continue**. Under **Authentication**, choose **No sign in**.
5. Click **Add**.

To use it in a chat: click the **+** button at the lower left of the message box, choose **Connectors**, and turn on **VitaminDWiki**.

On a Claude Team or Enterprise plan, an administrator of your organization may need to add it for you.

### ChatGPT

1. Go to **chatgpt.com/plugins**.
2. Click the **+** button, then **Add custom MCP server**.
3. For the name, type **VitaminDWiki**, and add a short description such as "Vitamin D research".
4. Under **Connection**, paste the address above.
5. If you are asked about authentication, choose no authentication (VitaminDWiki needs no sign-in).
6. Read the warning, select **I understand and want to continue**, then **Create as a plugin**.

To use it in a chat: type **@** in the message box and choose **VitaminDWiki**.

Which ChatGPT plans allow this can change. If you don't see these options, your plan may not include them.

---

### Questions to try

- *What does VitaminDWiki say about vitamin D and asthma in children?*
- *Find meta-analyses on vitamin D and depression.*
- *What are the newest VitaminDWiki pages about COVID?*
- *Summarize the overview page on vitamin D and pregnancy.*

You can ask in your own words. Mentioning "VitaminDWiki" helps your assistant use it.

---

### Good to know

- **It summarizes research, it isn't medical advice.** Your assistant will point you to what VitaminDWiki's pages say. Talk to your doctor about your own situation, especially about doses if you have kidney problems, high blood calcium, sarcoidosis or take other medications.
- **Check the links.** Your assistant should link every claim to a VitaminDWiki page. Open them to read the details.
- **Privacy:** your conversation stays between you and ChatGPT or Claude. VitaminDWiki only sees the searches your assistant makes and the pages it reads, not your messages.
- The menus in ChatGPT and Claude change from time to time, so the names above may differ slightly.
"""


def create_page(apps, schema_editor):
    from core.models import derive_content_fields

    SitePage = apps.get_model('pages', 'SitePage')
    if SitePage.objects.filter(slug=SLUG).exists():
        return
    markdown = content_md()
    content_html, content_text, character_count = derive_content_fields(markdown)
    SitePage.objects.create(
        title=TITLE,
        slug=SLUG,
        page_type='custom',
        is_published=True,
        meta_description=META_DESCRIPTION,
        content_md=markdown,
        content_html=content_html,
        content_text=content_text,
        character_count=character_count,
        public_modified_date=timezone.now(),
    )


def remove_page(apps, schema_editor):
    apps.get_model('pages', 'SitePage').objects.filter(slug=SLUG, title=TITLE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('pages', '0002_sitepage_public_modified_date'),
    ]

    operations = [
        migrations.RunPython(create_page, remove_page),
    ]
