import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sqb', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='sqbpartdefinition',
            name='digital_form_key',
            field=models.CharField(blank=True, default='', help_text='If set, the supervisor fills this in on screen instead of uploading a scanned copy \u2014 see sqb/digital_forms.py', max_length=50),
        ),
        migrations.CreateModel(
            name='DigitalFormEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('form_key', models.CharField(max_length=50)),
                ('header_data', models.JSONField(blank=True, default=dict)),
                ('items_data', models.JSONField(blank=True, default=dict)),
                ('comments', models.TextField(blank=True, default='')),
                ('filled_at', models.DateTimeField(auto_now=True)),
                ('part_upload', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='digital_form', to='sqb.sqbpartupload')),
            ],
        ),
    ]
