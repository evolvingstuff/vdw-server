# S3/CloudFront Setup for Media Files

This Django project is configured to use Amazon S3 and CloudFront for media file storage instead of local storage.

## Why S3/CloudFront?

- **No local storage needed**: Media files don't take up server space
- **CDN delivery**: Fast global content delivery via CloudFront
- **Automatic backups**: S3 provides 99.999999999% durability
- **Scalability**: No server disk space limitations

## Configuration

### 1. AWS Setup

1. Create an S3 bucket for media files (e.g., `vitamindwiki-media`)
2. Configure bucket for public read access
3. Set up CloudFront distribution pointing to the S3 bucket (optional but recommended)
4. Create IAM user with S3 write permissions

### 2. Environment Variables

Set these in your `.env` file or as system environment variables:

```bash
# Enable S3 storage
USE_S3_STORAGE=True

# AWS Credentials
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key

# S3 Configuration
AWS_STORAGE_BUCKET_NAME=vitamindwiki-media
AWS_S3_REGION_NAME=us-east-1

# Optional: CloudFront domain (faster delivery)
AWS_CLOUDFRONT_DOMAIN=d1234567890.cloudfront.net
```

### 3. IAM Policy

Your IAM user needs these permissions on the S3 bucket:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:ListBucket",
                "s3:GetBucketLocation"
            ],
            "Resource": "arn:aws:s3:::vitamindwiki-media"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:PutObjectAcl",
                "s3:GetObject",
                "s3:GetObjectAcl",
                "s3:DeleteObject"
            ],
            "Resource": "arn:aws:s3:::vitamindwiki-media/*"
        }
    ]
}
```

## How It Works

1. When `USE_S3_STORAGE=True`, Django uses `django-storages` with `boto3`
2. File uploads through Django admin go directly to S3
3. Media URLs point to CloudFront (or S3 directly if no CloudFront)
4. No media files are stored on the server

## Development vs Production

- **Development** (USE_S3_STORAGE=False): Files saved to local `media/` directory
- **Production** (USE_S3_STORAGE=True): Files uploaded to S3/CloudFront

## Testing S3 Configuration

```python
# In Django shell
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

# Test upload
path = default_storage.save('test.txt', ContentFile(b'Hello S3!'))
print(f"File saved to: {path}")
print(f"File URL: {default_storage.url(path)}")

# Test delete
default_storage.delete(path)
```

## Troubleshooting

1. **Access Denied**: Check IAM permissions and bucket policy
2. **File not accessible**: Ensure bucket has public read permissions or CloudFront is configured
3. **Slow uploads**: Consider using S3 Transfer Acceleration
4. **CORS issues**: Configure CORS on S3 bucket if needed