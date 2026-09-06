from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('game', '0025_sitesettings_check_schedule'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LeagueDeathBonus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('bonus_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='league_death_decisions', to='game.bonustype')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_league_death_bonus_decisions', to=settings.AUTH_USER_MODEL)),
                ('death', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='league_bonus_decisions', to='game.death')),
                ('league', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='death_bonus_decisions', to='game.league')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_league_death_bonus_decisions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Assegnazione bonus decesso di lega',
                'verbose_name_plural': 'Assegnazioni bonus decesso di lega',
            },
        ),
        migrations.AddConstraint(
            model_name='leaguedeathbonus',
            constraint=models.UniqueConstraint(fields=('league', 'death', 'bonus_type'), name='unique_league_death_bonus_decision'),
        ),
        migrations.AddIndex(
            model_name='leaguedeathbonus',
            index=models.Index(fields=['league', 'death'], name='game_ldb_league_death_idx'),
        ),
        migrations.AddIndex(
            model_name='leaguedeathbonus',
            index=models.Index(fields=['league', 'bonus_type'], name='game_ldb_league_bonus_idx'),
        ),
    ]
