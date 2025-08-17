# Server Migration Plan: VitaminDWiki from Static to Dynamic

## Current Situation

### Problems with Current Approach
- **Hugo build failures**: Single malformed file crashes entire 14k page build
- **Build time**: 3+ minutes for full site, memory intensive
- **Error messages**: Hugo gives cryptic errors without identifying problematic files
- **TikiWiki hosting**: $300/month for 40k visitors (10x overpriced)

### Requirements for New System
- Admin interface for dad to add/edit/remove pages
- Live markdown preview while editing
- Draft vs Published workflow
- Metadata as database fields (not front matter)
- Complex search (text + tag combinations)
- Handle 14k+ existing pages
- Automated backups
- ~40k visitors/month capacity

## Proposed Solution: Django on AWS

### Why Django?
- Built-in admin interface with authentication
- Native draft/published workflow
- PostgreSQL for concurrent users (not SQLite)
- Markdown preview via django-markdownx
- Robust, battle-tested for content sites
- Can reuse existing markdown conversion logic

### Infrastructure: AWS Lightsail
- **Cost**: $10-20/month (vs current $300)
- **Specs**: 2GB RAM, 2 vCPUs, 30GB SSD
- **Why Lightsail over EC2**: 
  - Simpler pricing
  - Automated backups included
  - Pre-configured Django blueprints
  - Still full server access

### S3 for Images/Attachments
- **YES, Lightsail works perfectly with S3**
- Store all images/PDFs on S3 (you already have this)
- Django serves image URLs pointing to S3/CloudFront
- Benefits:
  - No need to backup images with server
  - CDN delivery for images
  - Cheaper than storing on server
  - Already implemented in current setup

## Database Schema

```python
class Post(models.Model):
    # Core fields
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)  # Auto-generated but editable
    
    # Content fields (cached for performance)
    content_md = models.TextField()  # Raw markdown content
    content_html = models.TextField()  # Cached HTML rendering
    content_text = models.TextField()  # Plain text for search indexing
    
    # Metadata (replaces front matter)
    status = models.CharField(choices=[('draft','Draft'), ('published','Published')])
    created_date = models.DateTimeField()
    modified_date = models.DateTimeField()
    
    # Tag system with ontology support
    tags = models.ManyToManyField('Tag')  # Explicitly set by user
    derived_tags = models.ManyToManyField('Tag')  # tags + ontology-implied tags
    
    # SEO/Display
    meta_description = models.TextField(blank=True)
    
    # Migration fields (not editable in admin)
    original_page_id = models.IntegerField(null=True, blank=True)  # From TikiWiki
    aliases = models.TextField(blank=True)  # Old URLs for redirects
```

### Content Processing Pipeline
- **On save**: content_md → content_html (for display) + content_text (for search)
- **Tags**: When tags are set, derived_tags automatically includes them plus ontology-implied tags
- **Performance**: No markdown parsing on page views, just serve cached content_html

### Live Markdown Preview Implementation
- **Approach**: Server-side rendering via AJAX (no external JS libraries)
- **Why server-side**:
  - Ensures preview matches exactly what gets saved
  - Avoids CDN dependencies and CSP issues
  - Uses same markdown2 library as backend
- **Components**:
  1. Django view endpoint at `/admin/preview-markdown/`
  2. Accepts POST with markdown text
  3. Returns rendered HTML using markdown2
  4. JavaScript in admin sends debounced AJAX requests
  5. Updates preview pane with returned HTML
- **Benefits**:
  - No external dependencies
  - Works within Django's security constraints
  - Preview is 100% accurate to saved version
  - Simple and maintainable

## Migration Path

### Phase 1: Proof of Concept (1 day)
1. Set up minimal Django on Lightsail
2. Basic Post model with markdown editor
3. Configure S3 for image serving
4. Dad tests workflow with 5-10 pages
5. Verify admin interface meets needs

### Phase 2: Template Migration (2-3 days)
1. Port Hugo templates to Django templates
   - `{{ .Title }}` → `{{ post.title }}`
   - Pagination logic adjustment
2. Maintain URL structure if needed
3. Port custom shortcodes

### Phase 3: Data Migration (1-2 days)
1. Script to convert 14k markdown files → PostgreSQL
2. Extract front matter → database fields
3. Preserve tags, categories, dates
4. Map image references to S3 URLs

### Phase 4: Search Implementation (1 day)
1. PostgreSQL full-text search setup
2. Combined text + tag search
3. Search index optimization
4. Consider Elasticsearch if needed later

### Phase 5: Deployment & Testing (1 day)
1. Deploy to production Lightsail
2. Configure automated backups (database only)
3. Set up monitoring (UptimeRobot)
4. DNS migration
5. Test with subset of real traffic

## Ongoing Maintenance

### What Changes for Dad
- **Before**: Edit markdown files directly
- **After**: Login to `/admin`, use WYSIWYG editor
- **Better**: Can edit metadata without touching YAML
- **Better**: Visual preview while editing
- **Better**: Save drafts before publishing

### File Upload Strategy
- **Django-storages integration**: Automatic S3 uploads from admin
- **How it works for dad**:
  1. Clicks "Choose File" in admin interface
  2. Selects image/PDF from computer
  3. Django automatically uploads to S3
  4. Returns CloudFront URL for use in content
- **Drag & drop in markdown editor**: 
  - Drop images directly into editor
  - Auto-uploads to S3 in background
  - Inserts `![](cloudfront-url)` automatically
- **No local storage**: Files go straight to S3/CloudFront
- **Organized structure**: Files organized by year/month in S3

### Backup Strategy
- **Database backups to S3**:
  - Daily cron job: `pg_dump` → compress → upload to S3
  - Separate S3 bucket for backups (e.g., `vitamindwiki-backups`)
  - 30-day retention policy (auto-delete old backups)
  - Size: ~50-100MB compressed for 14k posts
  - Can restore to any point in last 30 days
- **Images**: Already on S3, versioned
- **Code**: Git repository
- **Server**: Lightsail snapshots weekly (includes full system)

### Disaster Recovery
- **Database failure**: Restore from S3 backup (< 5 minutes)
- **Server failure**: Spin up new Lightsail, restore DB from S3
- **S3 failure**: Unlikely, but S3 has 99.999999999% durability
- **Total recovery time**: < 1 hour for complete rebuild

### Performance Expectations
- 40k visitors/month = ~50 requests/minute peak
- Django + PostgreSQL handles 1000x this easily
- Page load: <100ms (vs 3 minute builds)
- No more build failures

## Cost Comparison

### Current (TikiWiki)
- Hosting: $300/month
- Build failures: Hours of debugging
- Total: ~$300/month + frustration

### Proposed (Django/Lightsail)
- Lightsail: $10-20/month
- S3 storage: ~$5/month (existing)
- CloudFront: ~$5/month (existing)
- Backups: ~$1/month
- Total: ~$25/month

**Savings: $275/month ($3,300/year)**

## Alternatives Considered & Rejected

1. **Keep Hugo, fix errors**: Still have 3+ minute builds, will hit new errors
2. **Eleventy/other static generators**: Same fundamental pagination problem
3. **Headless CMS + Static**: Still have build times
4. **WordPress**: Overkill, security nightmare
5. **Cloud functions**: Not truly dynamic, complex

## Next Steps

1. Get dad's buy-in on approach
2. Set up proof of concept for workflow testing
3. Only proceed with full migration if POC successful

## Open Questions

1. Custom domain requirements?
2. Email notifications needed?
3. User comments/interaction features?
4. Multi-language support needed?
5. API access requirements?