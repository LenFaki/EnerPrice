<p align="center">
  <img src="https://raw.githubusercontent.com/LenFaki/home-assistant-nl-day-ahead-prices/main/brand/enerprice-header-nl.png" alt="EnerPrice - Dynamische energieprijzen voor Home Assistant" width="100%">
</p>

# EnerPrice

**Taal:** [English](README.md) | Nederlands

**Ontwikkelpreview:** [Daily Energy Advisor PR3](docs/daily-advisor-pr3.md) voegt
HA-ochtendmeldingen met expliciete toestemming en begrensde energiecontrole toe.
Voorspeld en werkelijk blijven gescheiden; potentieel verschil is geen bewezen besparing.

EnerPrice is een HACS-compatibele custom integratie voor Home Assistant voor
Nederlandse dynamische energieprijzen. De integratie heette eerder **NL Day
Ahead Prices**. De repositorynaam en het integratiedomein
`nl_day_ahead_prices` blijven ongewijzigd voor achterwaartse compatibiliteit.

EnerPrice levert day-ahead stroomprijzen, kwartierprijzen, leveranciersspecifieke
all-in tarieven, prijsanalyses, beste periodes, trends, prognoses en praktisch
energieadvies. De standaard biedzone is `NL`, de valuta is `EUR` en prijzen
worden als `EUR/kWh` aangeboden.

## Nieuw in EnerPrice 2.6 — Dagelijkse Energieadviseur

De optionele **Dagelijkse Energieadviseur** zoekt het voordeligste moment voor een flexibel apparaat op basis van gepubliceerde stroomprijzen. Met Forecast.Solar, P1 en zonneproductie kan EnerPrice daarnaast een **afzonderlijk voorwaardelijk zonnescenario** berekenen. Je kunt optioneel een dagelijkse melding instellen met expliciete toestemming en voorspellingen vergelijken met betrouwbare cumulatieve energiemetingen. EnerPrice schakelt zelf geen apparaten.

**Begin eenvoudig:** zet de Dagelijkse Energieadviseur aan, vul het apparaatvermogen en de duur in, en bekijk eerst het gewone prijsadvies. Voeg zon, meldingen en prestatiemeting pas toe wanneer je die wilt gebruiken.

**[Lees de volledige handleiding voor EnerPrice 2.6: instellen, gebruiken en problemen oplossen](docs/enerprice-v2-6-gebruikershandleiding.md).**

## EnerPrice v2.5 — Smart Setup en Smart Energy Advisor

V2.5 voegt een optionele **Smart Setup** toe aan de bestaande Smart Energy Advisor. Via de EnerPrice-opties kun je de P1/netvermogenssensor, actuele zonneproductie, gasprijssensor, één flexibele verbruiker met vermogen, rendementen, gasinhoud, overschotdrempel en adviestaal opslaan. De adviseur gebruikt deze waarden daarna automatisch. Expliciete waarden in een servicecall hebben altijd voorrang. Voor bestaande installaties blijft Smart Setup standaard uitgeschakeld, zodat v2.4.x-gedrag behouden blijft.

De Smart Energy Advisor

V2.4 voegt een leverancier- en apparaatonafhankelijke **Smart Energy Advisor**
toe. De actie `nl_day_ahead_prices.get_smart_energy_advice` combineert de
all-in stroomprijs van EnerPrice optioneel met Home Assistant-entiteiten voor:

- actuele zonneproductie;
- actueel netvermogen;
- actuele gasprijs in EUR/m³.

Voor tapwater vergelijkt EnerPrice geen EUR/kWh stroom rechtstreeks met EUR/m³
gas. Beide worden omgerekend naar **kosten per kWh bruikbare warmte**:

```text
elektrische warmte = stroomprijs / elektrisch rendement
gaswarmte = gasprijs per m³ / (kWh gas per m³ × gasrendement)
```

De aannames voor elektrisch rendement, gasrendement en energie-inhoud van gas
zijn instelbaar. Zo blijft de berekening bruikbaar voor verschillende
installaties. Controleer voor nauwkeurige resultaten de waarden van je eigen
installatie en contract.

De adviseur kan onder andere deze statussen teruggeven:

- `solar_surplus` — gebruik beschikbare eigen zonnestroom;
- `cheap_grid` — elektrisch verwarmen vanaf het net is nu voordeliger;
- `gas` — gas is op dit moment voordeliger;
- `wait` — een duidelijk goedkoper elektrisch prijsinterval komt eraan;
- `normal` — er zijn onvoldoende gegevens voor een sterker advies.

De adviseur kijkt ook vooruit naar beschikbare day-ahead prijzen. Daardoor kan
bijvoorbeeld tapwaterverwarming worden uitgesteld wanneer elektrisch verwarmen
later goedkoper wordt dan zowel elektrisch verwarmen nu als verwarmen met gas.

EnerPrice **schakelt zelf geen apparaten**. Home Assistant-automatiseringen
kunnen het advies gebruiken om bijvoorbeeld een Solyx Nemo of een andere
flexibele verbruiker aan te sturen zodra het apparaat in Home Assistant
beschikbaar is.

Voorbeeld:

```yaml
action: nl_day_ahead_prices.get_smart_energy_advice
data:
  gas_price_entity: sensor.jouw_actuele_gasprijs
  solar_power_entity: sensor.jouw_zonneproductie
  grid_power_entity: sensor.jouw_netvermogen
  gas_efficiency: 0.90
  electric_efficiency: 1.0
  language: nl
response_variable: energy_advice
```

Positief netvermogen wordt behandeld als afname en negatief netvermogen als
teruglevering. Wanneer een netvermogensensor beschikbaar is, gebruikt EnerPrice de werkelijk gemeten teruglevering als overschot. Zonneproductie en gemeten overschot blijven afzonderlijke begrippen. Zonder netmeting kan actuele zonneproductie als expliciete fallback worden gebruikt; `surplus_source` maakt zichtbaar of het om `measured_grid_export`, `solar_production_fallback` of `unknown` gaat. Ontbrekende of niet-beschikbare sensoren worden niet als nul geïnterpreteerd.

Bij gedeeltelijk overschot gebruikt `effective_electric_heat_cost_per_kwh` het kostenmodel `purchased_energy_only`: alleen het resterende netaandeel krijgt de actuele all-in inkoopprijs. Mogelijke gemiste terugleververgoeding van zelf gebruikte energie wordt niet meegerekend; dit is dus geen volledige economische kostenberekening.
Voor testen kunnen ook directe numerieke waarden worden meegegeven in plaats
van entiteiten.

## Prijsadviseur

`sensor.nl_day_ahead_price_advisor` combineert de huidige all-in prijs,
prijsscore, beoordeling, trend en volatiliteit tot het advies `excellent`,
`good`, `neutral`, `avoid` of `critical`. De adviseur kijkt vooruit naar
een duidelijk goedkoper interval en toont onder andere het volgende gunstige
tijdstip, de prijs, wachttijd, mogelijke procentuele besparing en de beste
komende prijs. Negatieve prijzen en kwartierprijzen worden ondersteund.

## Prijsscore

`sensor.nl_day_ahead_price_score` geeft een robuuste score van 0–100, waarbij
100 de goedkoopste beschikbare prijs is. Vandaag en, indien beschikbaar, morgen
worden meegenomen. De dagscore-sensoren vatten onder andere volatiliteit,
goedkoopste blokken, duurste periode en goedkope/negatieve minuten samen.

## Planners

EnerPrice bevat planners voor EV-laden, boilers/warmtepompboilers, batterijen,
teruglevering en flexibele apparaten. De response-acties zoeken gunstige
periodes maar schakelen apparaten niet zelfstandig.

- `find_best_charging_window`: laadplan op energiebehoefte, vermogen en deadline.
- `find_best_heating_window`: goedkoop verwarmingsvenster voor een opgegeven duur.
- `find_battery_strategy`: laad/ontlaadstrategie met rendement en SOC-grenzen.
- `find_best_export_window`: gunstig teruglevermoment.
- `find_best_appliance_window`: planning voor bijvoorbeeld wasmachine, droger,
  vaatwasser, pomp of vloerverwarming.
- `get_smart_energy_advice`: contextadvies met stroom, zon, net en gas.

## Dashboardgenerator

`nl_day_ahead_prices.generate_dashboard_yaml` maakt CasaRegie-geïnspireerde
Lovelace-YAML in compact, volledig of energy-advisor formaat. De generator
gebruikt native Home Assistant Sections, headings, tiles en Markdown.
Uitgebreidere layouts kunnen ApexCharts gebruiken. De generator probeert de
werkelijke EnerPrice entity-ID's uit het Entity Registry te gebruiken, ook
wanneer entiteiten zijn hernoemd.

## Automatiseringsgenerator

`nl_day_ahead_prices.generate_automation_yaml` maakt bewerkbare
voorbeeldautomatiseringen voor onder andere boilers, EV-laden, meldingen,
batterijen en apparaten. Controleer altijd entity-ID's en instellingen voordat
je gegenereerde YAML activeert.

## Achterwaartse compatibiliteit

- Het domein blijft `nl_day_ahead_prices`.
- Bestaande v1.x unique-ID's en entity-ID's blijven behouden.
- Bestaande prijsattributen en ApexCharts-formaten blijven beschikbaar.
- Nieuwe geavanceerde entiteiten zijn waar passend standaard uitgeschakeld.
- Leveranciersprofielen uit het oudere schema worden in het geheugen gemigreerd.

## Installatie

### HACS

EnerPrice is momenteel via HACS te installeren als custom repository. De
voorbereiding voor officiële opname in de standaardcatalogus van HACS is bezig.
Totdat die inzending is geaccepteerd, voeg je de repository handmatig toe:

1. Voeg deze repository toe als custom repository in HACS.
2. Kies categorie **Integration**.
3. Installeer **EnerPrice**.
4. Herstart Home Assistant.
5. Voeg EnerPrice toe via **Instellingen > Apparaten & diensten**.

De HACS-entiteit `update.nl_day_ahead_prices_update` betekent alleen dat de
custom repository is geïnstalleerd. De prijssensoren verschijnen pas nadat de
integratie zelf via Apparaten & diensten is toegevoegd.

### Handmatig

Kopieer `custom_components/nl_day_ahead_prices` naar de map
`custom_components` van Home Assistant en herstart Home Assistant.

## Configuratie

Standaard gebruikt EnerPrice:

- biedzone: `NL`;
- valuta: `EUR`;
- primaire provider: Nord Pool;
- ENTSO-E fallback: uitgeschakeld.

De integratie gebruikt de gedeelde aiohttp-websessie van Home Assistant,
asynchrone config entries, een options flow en een DataUpdateCoordinator.
Reguliere API-updates vinden ieder uur rond minuut 7 plaats. Updates op
kwartiergrenzen kunnen de sensorstatus verversen zonder opnieuw marktdata op te
halen.

Prijzen voor morgen zijn optioneel. Zolang die nog niet zijn gepubliceerd
blijven geldige prijzen voor vandaag beschikbaar. Geldige cachegegevens kunnen
worden gebruikt als providers tijdelijk niet beschikbaar zijn.

## Leverancier en all-in prijs

In de opties kies je een ingebouwd Nederlands dynamisch leveranciersprofiel of
**Eigen leverancier**. Belangrijke opties zijn:

- `selected_supplier`;
- `supplier_tariff_updates`: Automatisch of Alleen meegeleverd;
- `price_resolution`: automatisch, uur of kwartier;
- `energy_tax`;
- `vat`.

De all-in formule is:

```text
all_in = marktprijs × (1 + btw) + energiebelasting + inkoopvergoeding_incl_btw
```

Vaste maandelijkse abonnementskosten worden niet automatisch omgerekend naar
EUR/kWh, omdat dit geen variabele prijscomponent is.

### Automatisch tarievenregister

EnerPrice bevat een lokaal tarievenregister als fallback. In de modus
**Automatisch** kan maximaal eenmaal per 24 uur het centraal onderhouden en
gevalideerde EnerPrice-register worden opgehaald. Er worden geen
leverancierswebsites gescrapet. **Alleen meegeleverd** gebruikt uitsluitend de
meegeleverde gegevens voor die configuratie. Eigen tarieven hebben altijd
voorrang.

Diagnostische entiteiten tonen de versheid en herkomst van het actieve
leverancierstarief en of het register uit `remote`, `cached_remote` of
`bundled` komt.

Leverancierstarieven kunnen wijzigen en per contract verschillen. Controleer
daarom je actuele contract of tariefblad wanneer nauwkeurigheid belangrijk is.

## Uur- en kwartierprijzen

EnerPrice ondersteunt:

- `auto`: interval volgt het leveranciersprofiel;
- `hourly`: altijd uurwaarden;
- `quarter_hour`: altijd kwartierwaarden.

Bij omzetting van kwartieren naar uren wordt het gemiddelde van de beschikbare
kwartieren gebruikt. Bij omzetting van uur naar kwartier krijgt ieder kwartier
dezelfde bronprijs. De originele bronprijzen blijven beschikbaar via
`raw_prices`, `raw_prices_today`, `raw_prices_tomorrow` en
`raw_price_resolution`.

## Belangrijkste entiteiten

EnerPrice levert onder andere huidige en volgende marktprijs, huidige en
volgende all-in prijs, gemiddelden/minimum/maximum voor vandaag en morgen,
geselecteerde leverancier, effectief prijsinterval, provider, laatste
succesvolle update, Price Advisor, Price Score, dagscore, terugleveradvies en
Energy Opportunity.

Binaire sensoren omvatten onder andere prijzen-morgen-beschikbaar,
API/data-beschikbaar, beste prijsperiode, piekprijsperiode, goedkope energie nu,
dure energie nu en uitzonderlijke energiekans.

Geavanceerde analyse-entiteiten zijn standaard vaak uitgeschakeld. Activeer
alleen wat je nodig hebt via de apparaatpagina van de integratie.

## Energy Optimization Toolkit

De toolkit bevat prognoses voor meerdere tijdvensters, prijstrends,
drie- en vijfniveau-beoordelingen, volatiliteit en beste/piekperiodes.
Berekeningen werken met echte timestamps en ondersteunen daardoor uurprijzen,
kwartierprijzen en zomer-/wintertijddagen van 23 of 25 uur.

Runtime-instellingen voor duur, flexibiliteit, minimale tussenruimte en
trenddrempels zijn via de apparaatpagina aanpasbaar zonder Home Assistant te
herstarten.

## Grafieken

`nl_day_ahead_prices.export_chart_data` levert `{time, price}`-gegevens voor
markt- of all-in prijzen. `generate_apexcharts_config` maakt een
ApexCharts-configuratie met marktprijs, all-in prijs en optioneel beste- en
piekperiodes.

Prijsitems gebruiken dit formaat:

```json
{
  "time": "2026-07-02T13:00:00+02:00",
  "price": 0.123456
}
```

## Probleemoplossing

### Ik zie alleen `update.nl_day_ahead_prices_update`

Deze entiteit komt van HACS. Herstart Home Assistant en voeg **EnerPrice** toe
via **Instellingen > Apparaten & diensten > Integratie toevoegen**. Controleer
bij problemen **Instellingen > Systeem > Logboeken** op
`nl_day_ahead_prices`.

### Morgenprijzen ontbreken

Dat is normaal zolang de markt de prijzen voor morgen nog niet heeft
gepubliceerd. EnerPrice blijft de geldige gegevens voor vandaag gebruiken.

### Smart Energy Advisor geeft geen warmtebronadvies

Controleer of de gasprijs in EUR/m³ staat en of zonne- en netvermogens in watt
worden aangeleverd. Controleer ook de tekenconventie van je netmeter. Voor de
service geldt: positief = afname, negatief = teruglevering. Pas rendementen en
de zonne-overschotdrempel aan op je eigen installatie.


## Gedetailleerde v2.3-functionaliteit

### EV-laadplanner

`find_best_charging_window` berekent op basis van gewenste energie, maximaal laadvermogen en deadline een geschikt laadvenster. De planner kan aaneengesloten laden afdwingen, korte sessies vermijden en rekening houden met de effectieve uur- of kwartierresolutie.

### Boilerplanner

`find_best_heating_window` zoekt een goedkoop aaneengesloten verwarmingsvenster voor de opgegeven duur en deadline. Dit is bedoeld als planningsadvies; EnerPrice schakelt de boiler of warmtepompboiler niet zelf.

### Batterijstrategie

`find_battery_strategy` gebruikt batterijcapaciteit, actuele en minimale/maximale SOC, laad- en ontlaadvermogen en roundtrip-rendement om laad- en ontlaadmomenten te adviseren. Netladen en terugleveren zijn afzonderlijk instelbaar.

### Terugleveradvies

`find_best_export_window` zoekt een gunstig moment om energie terug te leveren. Het advies kan rekening houden met de leveranciersspecifieke verkoopvergoeding en blijft gescheiden van advies over eigen verbruik.

### Apparatenplanner

`find_best_appliance_window` plant flexibele verbruikers zoals wasmachine, droger, vaatwasser of pomp binnen een opgegeven tijdvenster. Een energieverbruik in kWh kan optioneel worden meegegeven.

### Voorbeeldautomatiseringen

De planners en adviseurs leveren informatie; Home Assistant voert de daadwerkelijke acties uit. Gebruik de response van een service in een automation en controleer altijd de doel-entiteit, deadline en veiligheidsvoorwaarden voordat je automatisch schakelen activeert.

## Opties

EnerPrice ondersteunt naast provider- en leverancierinstellingen onder andere instellingen voor prijsresolutie, energiebelasting, btw, leveranciersprofiel, beste-periode-duur en flexibiliteit, piekperiode-duur en flexibiliteit en trenddrempels. Opties die alleen analyse beïnvloeden kunnen tijdens runtime worden toegepast; providerwijzigingen kunnen een reload vereisen.

## Runtime-instellingen

Runtime-instellingen voor analyse en periodeberekeningen zijn via de apparaatpagina aanpasbaar zonder Home Assistant volledig te herstarten. Hiermee kunnen onder andere duur, flexibiliteit, minimale tussenruimte en trenddrempels worden afgestemd op de installatie.

## Services

Naast de Smart Energy Advisor zijn de belangrijkste response-services:

- `nl_day_ahead_prices.export_chart_data` — prijsdata exporteren voor grafieken;
- `nl_day_ahead_prices.generate_apexcharts_config` — voorbeeld-YAML voor ApexCharts;
- `find_best_charging_window` — EV-laadvenster;
- `find_best_heating_window` — verwarmingsvenster;
- `find_battery_strategy` — batterijstrategie;
- `find_best_export_window` — teruglevervenster;
- `find_best_appliance_window` — flexibel apparaat plannen;
- `generate_dashboard_yaml` — Lovelace-dashboard genereren;
- `generate_automation_yaml` — voorbeeldautomation genereren;
- `get_smart_energy_advice` — stroom/zon/net/gas-contextadvies.

Wanneer meerdere EnerPrice-configuraties geladen zijn, kan de Smart Energy Advisor via `config_entry_id` expliciet aan de gewenste configuratie worden gekoppeld.

## Attributen

Prijsentiteiten bieden onder andere `prices`, `prices_today`, `prices_tomorrow`, `all_in_prices`, `all_in_prices_today`, `all_in_prices_tomorrow`, de overeenkomstige `raw_*` attributen, prijsresolutie, provider, fallbackstatus, cache-informatie, datacompleetheid, huidig/volgend interval, markt- en all-in prijs, beoordeling, trend, volatiliteit, dagstatistieken en leverancierstariefmetadata.

Prijsitems gebruiken steeds het formaat `{time, price}`, waarbij de prijs in EUR/kWh staat. Grote prijsarrays zijn bedoeld voor dashboards en analyse en worden waar passend buiten Recorder gehouden.

## ApexCharts-voorbeeld

Voor een all-in grafiek in ct/kWh kan `all_in_prices` rechtstreeks worden omgerekend:

```yaml
unit: ct/kWh
data_generator: |
  const prices = entity.attributes.all_in_prices ?? [];
  return prices.map(p => [
    new Date(p.time).getTime(),
    parseFloat((p.price * 100).toFixed(2))
  ]);
```

Dezelfde structuur werkt voor uur- en kwartierprijzen; gebruik `prices_today` voor marktprijzen van vandaag of `all_in_prices_today` voor all-in prijzen van vandaag.

## Migratie vanaf hass-entso-e

EnerPrice behoudt het belangrijke ApexCharts-formaat waarbij `entity.attributes.prices` een array van `{time, price}` blijft. Het domein verandert ten opzichte van hass-entso-e naar `nl_day_ahead_prices`, Home Assistant maakt nieuwe entity-ID's aan, prijzen worden genormaliseerd naar EUR/kWh en ENTSO-E is alleen een optionele fallback. Werk bestaande automations en dashboards bij naar de nieuwe EnerPrice entity-ID's.

## Snelstart: dashboardvoorbeelden

Voor direct bruikbare Home Assistant-kaarten, Smart Energy Advisor-voorbeelden, load-aware tapwateradvies en een veilige inrichtingschecklist: zie [Dashboardvoorbeelden](docs/dashboard-recipes.nl.md). Deze voorbeelden vormen tegelijk de bouwstenen voor de geplande begeleide onboarding-wizard.

## Privacy en werking

EnerPrice heeft geen cloudaccount nodig voor de integratie zelf. Marktprijzen
worden opgehaald bij de geconfigureerde prijsproviders. In automatische
tariefmodus kan het gevalideerde EnerPrice-tarievenregister worden opgehaald.
Smart Energy Advisor leest alleen de Home Assistant-entiteiten die je zelf via de service of Smart Setup selecteert en stuurt geen apparaat rechtstreeks aan.

## Bijdragen en releases

Release-informatie staat in [CHANGELOG.md](CHANGELOG.md). Voor het
releaseproces en automatische publicatie na succesvolle CI:
[docs/releases.md](docs/releases.md).

Issues en pull requests zijn welkom via GitHub.

## Licentie

Zie [LICENSE](LICENSE) voor de licentievoorwaarden.

## V2.5 Smart Setup snel controleren

Na het inschakelen van **Smart Setup** maakt EnerPrice een blijvende **Smart Energy Advisor**-sensor aan. Deze ververst bij EnerPrice-prijsupdates en wanneer de ingestelde P1/netvermogen-, zonneproductie- of gasprijssensor verandert. Het gegenereerde Smart Energy-dashboard gebruikt de werkelijke entity-ID uit het Entity Registry.

Controleer vóór gebruik van het advies de invoer in Home Assistant: netvermogen in W met positief = afname en negatief = teruglevering, zonneproductie in W en gasprijs in EUR/m³. Ontbrekende of niet-beschikbare waarden blijven onbekend en worden niet als nul behandeld. V2.5 ondersteunt in Smart Setup één flexibele verbruiker en blijft uitsluitend adviserend; EnerPrice schakelt de verbruiker niet zelf.

Zie [Dashboardvoorbeelden](docs/dashboard-recipes.nl.md) voor een praktische controlevolgorde en dashboardvoorbeelden.
