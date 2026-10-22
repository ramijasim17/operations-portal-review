import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SQBJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('job_number', models.CharField(max_length=100, unique=True)),
                ('job_name', models.CharField(blank=True, default='', max_length=255)),
                ('created_date', models.DateField(blank=True, null=True)),
                ('permit_number', models.CharField(blank=True, default='', max_length=100)),
            ],
            options={
                'ordering': ['-created_date'],
            },
        ),
        migrations.CreateModel(
            name='SQBPartDefinition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150)),
                ('order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'ordering': ['order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='SQBTrackerEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(blank=True, null=True)),
                ('client', models.CharField(blank=True, default='', max_length=255)),
                ('job_ticket', models.CharField(blank=True, default='', max_length=100)),
                ('npt_hours', models.FloatField(blank=True, null=True)),
                ('description', models.TextField(blank=True, default='')),
                ('rating', models.CharField(blank=True, default='', max_length=50)),
            ],
            options={
                'ordering': ['-date'],
            },
        ),
        migrations.CreateModel(
            name='SQBType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='SQBDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('doc_type', models.CharField(max_length=150)),
                ('file', models.FileField(blank=True, null=True, upload_to='sqb_docs/')),
                ('notes', models.TextField(blank=True, default='')),
                ('uploaded_date', models.DateField(auto_now_add=True)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='documents', to='sqb.sqbjob')),
            ],
        ),
        migrations.CreateModel(
            name='SQBPackage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('job_name', models.CharField(help_text='Matches a Daily Report job name', max_length=255)),
                ('status', models.CharField(choices=[('Open', 'Open'), ('Closed', 'Closed')], default='Open', max_length=20)),
                ('created_date', models.DateField(auto_now_add=True)),
                ('closed_date', models.DateField(blank=True, null=True)),
                ('combined_file', models.FileField(blank=True, null=True, upload_to='sqb_combined/')),
                ('closed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sqb_packages_closed', to=settings.AUTH_USER_MODEL)),
                ('sqb_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='packages', to='sqb.sqbtype')),
            ],
            options={
                'ordering': ['-created_date'],
                'unique_together': {('job_name', 'sqb_type')},
            },
        ),
        migrations.CreateModel(
            name='SQBPartUpload',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(blank=True, null=True, upload_to='sqb_parts/', validators=[django.core.validators.FileExtensionValidator(allowed_extensions=['pdf'])])),
                ('uploaded_date', models.DateField(blank=True, null=True)),
                ('package', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parts', to='sqb.sqbpackage')),
                ('part_definition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='sqb.sqbpartdefinition')),
            ],
            options={
                'ordering': ['part_definition__order'],
                'unique_together': {('package', 'part_definition')},
            },
        ),
        migrations.AddField(
            model_name='sqbpartdefinition',
            name='sqb_type',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parts', to='sqb.sqbtype'),
        ),
        migrations.AlterUniqueTogether(
            name='sqbpartdefinition',
            unique_together={('sqb_type', 'name')},
        ),
    ]
