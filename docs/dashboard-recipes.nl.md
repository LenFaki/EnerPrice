# EnerPrice dashboardvoorbeelden

Deze voorbeelden zijn bedoeld als direct bruikbaar startpunt voor Home Assistant. Vervang de voorbeeld-entiteiten door de entity-ID's uit je eigen installatie. EnerPrice blijft adviserend: de voorbeelden sturen niet zelfstandig een apparaat aan, tenzij je daar bewust zelf een automatisering omheen bouwt.

## 1. Huidige all-in stroomprijs

Gebruik een native Tile-kaart:

```yaml
type: tile
entity: sensor.nl_day_ahead_prices_current_all_in_price
name: Stroomprijs nu
```

Wijkt jouw entity-ID af, kies dan **Huidige All-in Prijs** op de EnerPrice-apparaatpagina.

## 2. Prijsadviseur

```yaml
type: tile
entity: sensor.nl_day_ahead_prices_price_advisor
name: Prijsadvies
```

De Prijsadviseur geeft snel prijsadvies en heeft geen gas-, zonne- of P1-sensor nodig.

## 3. Smart Setup en slim tapwateradvies

Vanaf v2.5 kun je **Smart Setup** optioneel inschakelen via de EnerPrice-opties. Daar kun je de P1/netvermogenssensor, actuele zonneproductie, gasprijssensor, naam en vermogen van één flexibele verbruiker, rendementen, gasinhoud, overschotdrempel en adviestaal opslaan. De Smart Energy Advisor gebruikt deze waarden daarna automatisch; je hoeft dezelfde entity-ID's en instellingen dus niet bij iedere actie opnieuw mee te geven.

Smart Setup is optioneel en staat voor bestaande installaties standaard uit. Bestaande v2.4.x-servicecalls blijven werken. Waarden die je expliciet in een servicecall meegeeft hebben altijd voorrang op de opgeslagen Smart Setup-instellingen.

De Smart Energy Advisor is een response-actie.

De Smart Energy Advisor is een response-actie. Een praktische inrichting gebruikt:
- een werkende EnerPrice-configuratie met actuele all-in prijzen;
- optioneel een gasprijssensor in €/m³;
- bij voorkeur een P1/netvermogenssensor in W;
- optioneel het elektrische vermogen van het flexibele apparaat in W.

Voor netvermogen verwacht EnerPrice **positief = afname** en **negatief = teruglevering**. `unknown`, `unavailable` en ontbrekende waarden worden niet als nul behandeld.

Wanneer P1/netvermogen beschikbaar is, is teruglevering de gemeten basis voor het overschot. Ontbreekt netvermogen maar is actuele zonneproductie beschikbaar, dan gebruikt EnerPrice die alleen als expliciete fallback (`surplus_source: solar_production_fallback`). Dit is productie en **geen gemeten netto-overschot**, omdat huishoudelijk verbruik dan onbekend is. Zonder beide bronnen blijft `surplus_source` `unknown`.

Voorbeeld:

```yaml
action: nl_day_ahead_prices.get_smart_energy_advice
data:
  gas_price_entity: sensor.jouw_gasprijs
  grid_power_entity: sensor.jouw_netvermogen
  flexible_load_power_w: 1500
  electric_efficiency: 1.0
  gas_efficiency: 0.90
  gas_kwh_per_m3: 9.769
  solar_surplus_threshold_w: 500
  language: nl
```

Met `flexible_load_power_w` maakt EnerPrice onderscheid tussen volledig en gedeeltelijk overschot. Bij een apparaat van 1500 W is 500 W teruglevering dus 33,3% dekking en niet langer automatisch voldoende om volledig op overschot te draaien. Het resterende netaandeel wordt meegenomen in de effectieve elektrische warmtekosten.

Handige antwoordvelden zijn `state`, `recommendation`, `cheapest_now`, `electric_heat_cost_per_kwh`, `effective_electric_heat_cost_per_kwh`, `gas_heat_cost_per_kwh`, `measured_solar_surplus_w`, `surplus_source`, `partial_surplus`, `surplus_coverage_percent`, `required_surplus_w`, `cost_model`, `next_better_time` en `minutes_until_better`.

`effective_electric_heat_cost_per_kwh` gebruikt in v2.5 het model `purchased_energy_only`: beschikbaar overschot krijgt geen extra inkoopkosten en alleen het resterende netaandeel wordt tegen het actuele all-in tarief gerekend. Dit is **geen volledige economische kostenberekening**; mogelijke gemiste terugleververgoeding/opportunity cost van zelf gebruikte energie wordt niet meegerekend.

## 4. Een EnerPrice-dashboard genereren

Je hoeft een dashboard niet volledig zelf te schrijven. Voer uit:

```yaml
action: nl_day_ahead_prices.generate_dashboard_yaml
data:
  dashboard_type: energy_advisor
  include_market_price: true
  include_all_in_price: true
  include_supplier_info: true
  include_best_periods: true
  include_price_advisor: true
```

Kopieer de teruggegeven YAML naar een Home Assistant-dashboard. Ook `compact` en `full` zijn beschikbaar.

Vanaf fase 4 is de generator **configuratiebewust**. EnerPrice gebruikt de echte entity-ID's van de gekozen config entry in plaats van vaste namen. Als Smart Setup actief is, voegt het dashboard alleen de beschikbare context toe: P1/netvermogen, zonneproductie en/of gasprijs. De ingestelde flexibele verbruiker en het vermogen worden eveneens getoond. Ontbrekende onderdelen worden niet als lege kaarten weergegeven.

Bij meerdere EnerPrice-configuraties geef je `config_entry_id` mee. Met `language: auto` volgt het gegenereerde dashboard de taal van Home Assistant; `nl` en `en` kunnen ook expliciet worden gekozen. Met `include_smart_energy: false` kun je het Smart Energy-blok bewust weglaten.

De generator maakt uitsluitend dashboard-YAML en schakelt geen apparaten. De Smart Energy Advisor blijft in deze fase adviserend; apparaatbesturing hoort bewust niet bij de dashboardgenerator.

## 5. Veilig automatiseren

Behandel de Smart Energy Advisor als advies en voeg eigen veiligheidsvoorwaarden toe voordat een echt apparaat wordt geschakeld. Behoud bij tapwater altijd de eigen thermostaat, legionella-/hygiënecyclus, maximale temperatuur, minimale draaitijd en beveiligingen van de fabrikant. Een niet-beschikbare sensor mag nooit als toestemming om in te schakelen worden geïnterpreteerd.

## Snelle inrichting

1. Installeer en configureer EnerPrice.
2. Controleer of **Huidige All-in Prijs** een plausibele waarde heeft.
3. Open de EnerPrice-opties en schakel desgewenst **Smart Setup** in.
4. Selecteer de P1/netvermogenssensor en controleer de tekenrichting.
5. Voeg een gasprijssensor toe als je warmtebronnen wilt vergelijken.
6. Selecteer actuele zonneproductie als aanvullende context/fallback gewenst is.
7. Vul het echte elektrische vermogen van het flexibele apparaat in zodra dat bekend is.
8. Test de Smart Energy Advisor handmatig via Ontwikkelaarstools > Acties; met Smart Setup hoeft de `data:`-sectie voor opgeslagen defaults niet opnieuw te worden ingevuld.
9. Genereer of kopieer een dashboardvoorbeeld.
10. Bouw pas daarna apparaat-automatiseringen met expliciete veiligheidsvoorwaarden.


## V2.5 praktische validatie

Schakel Smart Setup in en controleer daarna de blijvende **Smart Energy Advisor**-entiteit op de EnerPrice-apparaatpagina. Laat één ingestelde bron (P1/netvermogen, zonneproductie of gasprijs) veranderen en controleer of status en attributen van de adviseur mee verversen. Genereer vervolgens een `energy_advisor`-dashboard en controleer dat de werkelijke advisor entity-ID wordt gebruikt, alleen ingestelde optionele bronnen worden getoond en de weergave ook op mobiel bruikbaar blijft.

De verwachte statussen zijn `solar_surplus`, `cheap_grid`, `wait`, `gas` en `normal`. Controleer waar mogelijk zowel uur- als kwartierprijzen. Ontbrekende of niet-beschikbare invoer moet onbekend blijven en mag nooit als een foutief nulsignaal worden geïnterpreteerd. Expliciete waarden in `get_smart_energy_advice` blijven voorrang houden boven opgeslagen Smart Setup-standaarden.

V2.5 ondersteunt bewust één flexibele verbruiker en geeft alleen advies. Meerdere verbruikers, EV-specifieke Smart Setup-logica, batterijoptimalisatie en volledige optimalisatie van teruglever-/opportunity costs vallen buiten deze release.
