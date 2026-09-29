// Dhatu Drishti — dashboard interactivity
document.addEventListener('DOMContentLoaded', () => {

  // --- Nav link active state ---
  const navLinks = document.querySelectorAll('nav a');
  const menuToggle = document.querySelector('.menu-toggle');
  const navigation = document.querySelector('#main-navigation');

  if (menuToggle && navigation) {
    menuToggle.addEventListener('click', () => {
      const isOpen = navigation.classList.toggle('is-open');
      menuToggle.setAttribute('aria-expanded', String(isOpen));
      menuToggle.setAttribute('aria-label', isOpen ? 'Close navigation menu' : 'Open navigation menu');
    });
  }

  navLinks.forEach(link => {
    link.addEventListener('click', (e) => {
      navLinks.forEach(l => l.classList.remove('active'));
      link.classList.add('active');
      navigation?.classList.remove('is-open');
      menuToggle?.setAttribute('aria-expanded', 'false');
      menuToggle?.setAttribute('aria-label', 'Open navigation menu');
    });
  });

  // --- Live satellite map for the MOIL mining area ---
  const mapElement = document.getElementById('live-satellite-map');
  let mineMap;
  let satelliteLayer;
  let mineMarker;
  let mineMarkers = [];
  let mineOverlays = [];
  const stateSelect = document.getElementById('stateSelect');
  const districtSelect = document.getElementById('districtSelect');
  const mineSelect = document.getElementById('mineSelect');
  const manualMine = document.getElementById('manualMine');
  const manualGroup = document.getElementById('manualGroup');
  const mineInsight = document.getElementById('mine-insight');
  const mineWeather = document.getElementById('mine-weather');
  const zonePit = document.querySelector('[data-zone-name="pit"]');
  const zoneVegetation = document.querySelector('[data-zone-name="vegetation"]');
  const zoneStatuses = document.querySelectorAll('[data-zone-status]');
  const mines = {
    // Coordinates verified against Mindat locality records, MOIL/EC lease
    // documents and OSM/Nominatim — several of the originals were 20-120 km off.
    balaghat: { name: 'Balaghat Complex', center: [21.8497, 80.2267], pit: 'Balaghat-3 Pit', zone: 'Zone B - Vegetation', ndvi: '14%', weatherLabel: 'Balaghat mine belt' },
    bharveli: { name: 'Bharveli Mine', center: [21.8500, 80.2331], pit: 'Bharveli Pit', zone: 'Bharveli vegetation belt', ndvi: '12%', weatherLabel: 'Bharveli mine area' },
    tirodi: { name: 'Tirodi Mine', center: [21.6856, 79.7192], pit: 'Tirodi Pit', zone: 'Tirodi vegetation belt', ndvi: '11%', weatherLabel: 'Tirodi mine area' },
    ukwa: { name: 'Ukwa Mine', center: [21.9742, 80.4664], pit: 'Ukwa Pit', zone: 'Ukwa vegetation belt', ndvi: '9%', weatherLabel: 'Ukwa mine area' },
    sitasaongi: { name: 'Sitasaongi Mine', center: [21.5322, 79.7467], pit: 'Sitasaongi Pit', zone: 'Sitasaongi vegetation belt', ndvi: '12%', weatherLabel: 'Sitasaongi mine area' },
    gumgaon: { name: 'Gumgaon Mine', center: [21.4000, 78.9830], pit: 'Gumgaon Pit', zone: 'Gumgaon vegetation belt', ndvi: '10%', weatherLabel: 'Gumgaon mine area' },
    beldongri: { name: 'Beldongri Mine', center: [21.3403, 79.2922], pit: 'Beldongri Pit', zone: 'Beldongri vegetation belt', ndvi: '8%', weatherLabel: 'Beldongri mine area' },
    chikla: { name: 'Chikla Mine', center: [21.5431, 79.7539], pit: 'Chikla Pit', zone: 'Chikla vegetation belt', ndvi: '13%', weatherLabel: 'Chikla mine area' },
    kandri: { name: 'Kandri Mine', center: [21.4130, 79.2670], pit: 'Kandri Pit', zone: 'Kandri vegetation belt', ndvi: '7%', weatherLabel: 'Kandri mine area' },
    'dongri-buzurg': { name: 'Dongri Buzurg Mine', center: [21.5486, 79.6828], pit: 'Dongri Buzurg Pit', zone: 'Dongri Buzurg vegetation belt', ndvi: '11%', weatherLabel: 'Dongri Buzurg mine area' },
    munsar: { name: 'Munsar Mine', center: [21.4014, 79.2808], pit: 'Munsar Pit', zone: 'Munsar vegetation belt', ndvi: '9%', weatherLabel: 'Munsar mine area' },
    sitapatore: { name: 'Sitapatore-Sukli Mine', center: [21.7000, 79.6667], pit: 'Sitapatore-Sukli Pit', zone: 'Sitapatore-Sukli vegetation belt', ndvi: '10%', weatherLabel: 'Sitapatore-Sukli mine area' },
    netra: { name: 'Netra Manganese Mine', center: [21.8644, 79.9808], pit: 'Netra Pit', zone: 'Netra vegetation belt', ndvi: '8%', weatherLabel: 'Netra mine area' },
    ramrama: { name: 'Ramrama Manganese Mine', center: [21.8667, 79.9331], pit: 'Ramrama Pit', zone: 'Ramrama vegetation belt', ndvi: '9%', weatherLabel: 'Ramrama mine area' },
    ghondi: { name: 'Ghondi Manganese Mine', center: [21.9188, 80.4184], pit: 'Ghondi Pit', zone: 'Ghondi vegetation belt', ndvi: '12%', weatherLabel: 'Ghondi mine area' }
  };
  const mineFields = {
    // Districts reflect where each mine actually sits (verified by
    // reverse-geocoding Mindat lease coordinates). Netra, Ramrama and Ghondi
    // are all Balaghat district — they were previously listed under
    // Chhindwara/Jabalpur/Alirajpur/Jhabua, and Ghondi appeared twice.
    mp: {
      balaghat: [['all', 'All Balaghat mine areas'], ['balaghat', 'Balaghat Mine (MOIL)'], ['bharveli', 'Bharveli Mine (MOIL)'], ['ukwa', 'Ukwa Mine (MOIL)'], ['tirodi', 'Tirodi Mine (MOIL)'], ['sitapatore', 'Sitapatore-Sukli Mine (MOIL)'], ['netra', 'Netra Manganese Mine'], ['ramrama', 'Ramrama Manganese Mine'], ['ghondi', 'Ghondi Manganese Mine']]
    },
    mh: {
      nagpur: [['all', 'All Nagpur mine areas'], ['gumgaon', 'Gumgaon Mine (MOIL)'], ['beldongri', 'Beldongri Mine (MOIL)'], ['kandri', 'Kandri Mine (MOIL)'], ['munsar', 'Munsar Mine (MOIL)']],
      bhandara: [['all', 'All Bhandara mine areas'], ['chikla', 'Chikla Mine (MOIL)'], ['dongri-buzurg', 'Dongri Buzurg Mine (MOIL)'], ['sitasaongi', 'Sitasaongi Mine']]
    }
  };

  if (mapElement && window.L) {
    const moilCenter = mines.balaghat.center;

    // Sharp-zoom configuration -------------------------------------------------
    // Esri World_Imagery has real pixels up to z18 for this region (z19+ is a
    // blank placeholder), so never ask for tiles deeper than that and never
    // let Leaflet upscale them: with maxNativeZoom === maxZoom every tile is
    // shown 1:1 instead of being stretched (the old 16 → 24 config smeared
    // z16 pixels up to 8x). detectRetina fetches the tile one zoom deeper and
    // scales it down, which matches HiDPI screens pixel-for-pixel.
    const NATIVE_MAX_ZOOM = 18;
    const retina = window.devicePixelRatio > 1;
    const layerMaxZoom = NATIVE_MAX_ZOOM - (retina ? 1 : 0);

    mineMap = L.map(mapElement, {
      zoomControl: true,
      minZoom: 5,
      maxZoom: layerMaxZoom
    }).setView(moilCenter, 13);

    satelliteLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxNativeZoom: NATIVE_MAX_ZOOM,
      maxZoom: NATIVE_MAX_ZOOM,
      detectRetina: retina,
      attribution: 'Satellite tiles &copy; Esri'
    }).addTo(mineMap);

    // A CSS filter on the tile pane makes Chrome rasterise it once and then
    // reuse that bitmap while Leaflet scales the pane during a zoom, which
    // leaves NDVI/Thermal views fuzzy after zooming. Re-apply the filter on
    // zoom end to force a fresh raster at the new scale.
    const tilePane = mapElement.querySelector('.leaflet-tile-pane');
    const refreshPaneRaster = () => {
      if (!tilePane) return;
      const filtered = mapElement.classList.contains('view-thermal')
        || mapElement.classList.contains('view-ndvi');
      if (!filtered) return;
      tilePane.style.transition = 'none'; // don't animate the forced re-raster
      tilePane.style.filter = 'none';
      void tilePane.offsetHeight; // flush the un-filtered layout
      tilePane.style.filter = ''; // fall back to the class filter, at full res
      tilePane.style.transition = '';
    };
    mineMap.on('zoomend', refreshPaneRaster);

    renderMine(mines.balaghat);
  }

  function renderMine(mine) {
    if (!mineMap) return;
    if (mine === 'all') {
      renderAllMines();
      return;
    }
    mineMap.setView(mine.center, 14);
    if (mineMarker) mineMap.removeLayer(mineMarker);
    mineMarkers.forEach(marker => mineMap.removeLayer(marker));
    mineMarkers = [];
    mineOverlays.forEach(overlay => mineMap.removeLayer(overlay));
    mineOverlays = [];
    mineMarker = L.marker(mine.center).addTo(mineMap).bindTooltip(`MOIL ${mine.name} · live satellite view`, {
      permanent: true, direction: 'top', className: 'moil-map-label'
    });
    mineOverlays.push(L.circle([mine.center[0] + 0.006, mine.center[1] + 0.009], {
      radius: 520, color: '#F0B429', weight: 2, dashArray: '6 5', fillColor: '#F0B429', fillOpacity: 0.16
    }).addTo(mineMap).bindTooltip(`${mine.name} · vegetation attention`));
    mineOverlays.push(L.circle([mine.center[0] - 0.008, mine.center[1] - 0.011], {
      radius: 260, color: '#EF6461', weight: 2, dashArray: '6 5', fillColor: '#EF6461', fillOpacity: 0.18
    }).addTo(mineMap).bindTooltip(`${mine.name} · slope risk`));
    if (mineInsight) mineInsight.innerHTML = `Vegetation monitoring active for <span class="zone-ref">${mine.zone}</span>, with a ${mine.ndvi} decline from the baseline. Recommended: field inspection at ${mine.name}.`;
    if (zonePit) zonePit.textContent = mine.pit;
    if (zoneVegetation) zoneVegetation.textContent = mine.zone;
    zoneStatuses.forEach(status => {
      if (status.dataset.zoneStatus === 'pit') status.textContent = 'Normal';
      if (status.dataset.zoneStatus === 'vegetation') status.textContent = 'Attention';
      if (status.dataset.zoneStatus === 'slope') status.textContent = 'High risk';
    });
    loadMineWeather(mine);
  }

  function renderAllMines() {
    if (!mineMap) return;
    if (mineMarker) mineMap.removeLayer(mineMarker);
    mineMarkers.forEach(marker => mineMap.removeLayer(marker));
    mineMarkers = Object.values(mines).map(mine => L.marker(mine.center).addTo(mineMap).bindTooltip(`MOIL ${mine.name}`, { direction: 'top' }));
    mineOverlays.forEach(overlay => mineMap.removeLayer(overlay));
    mineOverlays = [];
    const bounds = L.latLngBounds(Object.values(mines).map(mine => mine.center));
    mineMap.fitBounds(bounds.pad(0.12));
    if (mineInsight) mineInsight.innerHTML = `Monitoring <span class="zone-ref">${Object.keys(mines).length} MOIL mine areas</span> across Maharashtra and Madhya Pradesh. Select an individual mine for detailed weather and risk analysis.`;
    if (mineWeather) mineWeather.textContent = 'Select an individual mine to view live local weather.';
    if (zonePit) zonePit.textContent = 'All MOIL mine areas';
    if (zoneVegetation) zoneVegetation.textContent = 'All vegetation zones';
  }

  async function loadMineWeather(mine) {
    if (!mineWeather) return;
    mineWeather.textContent = `Loading live weather · ${mine.weatherLabel}...`;
    try {
      const [latitude, longitude] = mine.center;
      const response = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation&timezone=Asia%2FKolkata`);
      if (!response.ok) throw new Error('Weather request failed');
      const current = (await response.json()).current;
      mineWeather.textContent = `${mine.name} weather · ${current.temperature_2m}°C · humidity ${current.relative_humidity_2m}% · wind ${current.wind_speed_10m} km/h · rain ${current.precipitation} mm`;
    } catch (error) {
      mineWeather.textContent = `Weather unavailable for ${mine.name}; satellite monitoring remains active.`;
    }
  }

  function populateDistricts() {
    if (!stateSelect || !districtSelect) return;
    const districts = Object.keys(mineFields[stateSelect.value]);
    districtSelect.innerHTML = districts.map(district => `<option value="${district}">${district[0].toUpperCase()}${district.slice(1)}</option>`).join('');
    populateFields();
  }

  function populateFields() {
    if (!districtSelect || !mineSelect) return;
    const fields = mineFields[stateSelect.value][districtSelect.value] || [];
    mineSelect.innerHTML = fields.map(([value, label]) => `<option value="${value}">${label}</option>`).join('') + '<option value="manual">Manual / Custom Field</option>';
    manualGroup?.classList.toggle('is-visible', mineSelect.value === 'manual');
  }

  async function applySelectedMine() {
    if (!mineSelect) return;
    if (mineSelect.value === 'manual') {
      const query = manualMine?.value.trim();
      if (!query) return;
      try {
        const response = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(`${query}, India`)}`);
        const [place] = await response.json();
        if (!place) throw new Error('Mine field not found');
        renderMine({ name: query, center: [Number(place.lat), Number(place.lon)], pit: query, zone: `${query} vegetation belt`, ndvi: 'live', weatherLabel: query });
      } catch (error) {
        if (mineWeather) mineWeather.textContent = `Could not locate ${query}; choose a listed mine area.`;
      }
      return;
    }
    renderMine(mineSelect.value === 'all' ? 'all' : (mines[mineSelect.value] || mines.balaghat));
  }

  if (stateSelect && districtSelect && mineSelect) {
    stateSelect.addEventListener('change', () => {
      populateDistricts();
      applySelectedMine();
    });
    districtSelect.addEventListener('change', () => {
      populateFields();
      applySelectedMine();
    });
    mineSelect.addEventListener('change', () => {
      manualGroup?.classList.toggle('is-visible', mineSelect.value === 'manual');
      applySelectedMine();
    });
    let manualTimer;
    manualMine?.addEventListener('input', () => {
      clearTimeout(manualTimer);
      if (mineSelect.value !== 'manual') return;
      manualTimer = setTimeout(applySelectedMine, 700);
    });
    populateDistricts();
  }

  if (mineMap) renderMine('all');

  // --- Map layer switcher (Satellite / NDVI / Thermal) ---
  const mapTools = document.querySelectorAll('.map-tool');
  let thermalOverlay = null;

  const viewOf = tool => (tool.dataset.view || tool.textContent || '').trim().toLowerCase();

  // Warm "heat" blobs drawn over the mine areas while the Thermal view is active.
  function buildThermalOverlay() {
    if (!mineMap || !window.L) return null;
    const group = L.layerGroup();
    const hotSpots = [[750, '#F0B429', 0.08, 0.55], [450, '#EF6461', 0.12, 0.6], [220, '#FF3B30', 0.28, 0.8]];
    Object.values(mines).forEach((mine, index) => {
      const surfaceTemp = (34 + (index % 6) * 2.5).toFixed(1);
      hotSpots.forEach(([radius, color, fillOpacity, opacity]) => {
        group.addLayer(L.circle(mine.center, {
          radius, color, weight: 1, fillColor: color, fillOpacity, opacity
        }).bindTooltip(`${mine.name} · ${surfaceTemp}°C surface temp`));
      });
    });
    return group;
  }

  function applyMapView(tool) {
    if (!tool) return;
    const view = viewOf(tool);
    mapTools.forEach(t => t.classList.toggle('active', t === tool));
    if (!mapElement || !mineMap) return;

    // Each view recolours the satellite tiles instead of swapping in a street map.
    mapElement.classList.toggle('view-ndvi', view === 'ndvi');
    mapElement.classList.toggle('view-thermal', view === 'thermal');

    if (view === 'thermal') {
      if (!thermalOverlay) thermalOverlay = buildThermalOverlay();
      if (thermalOverlay && !mineMap.hasLayer(thermalOverlay)) thermalOverlay.addTo(mineMap);
    } else if (thermalOverlay && mineMap.hasLayer(thermalOverlay)) {
      mineMap.removeLayer(thermalOverlay);
    }
  }

  if (mapTools.length) {
    mapTools.forEach(tool => {
      tool.setAttribute('role', 'button');
      tool.setAttribute('tabindex', '0');
      const activate = () => applyMapView(tool);
      tool.addEventListener('click', activate);
      tool.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          activate();
        }
      });
    });
    // Sync the map with whichever tool is marked active in the markup.
    applyMapView(document.querySelector('.map-tool.active') || mapTools[0]);
  }

  // --- Insight "View Analysis" link scrolls to the AI Analysis section ---
  const insightLink = document.querySelector('.insight-link');
  if (insightLink) {
    insightLink.addEventListener('click', () => {
      document.getElementById('analysis')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  // --- Hero CTA buttons ---
  const exploreBtn = document.querySelector('.btn-primary');
  if (exploreBtn) {
    exploreBtn.addEventListener('click', () => {
      document.getElementById('map')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  const aiAnalysisBtn = document.querySelector('.btn-ghost');
  if (aiAnalysisBtn) {
    aiAnalysisBtn.addEventListener('click', () => {
      document.getElementById('analysis')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  // --- Profile button placeholder ---
  const profileBtn = document.querySelector('.profile-btn');
  if (profileBtn) {
    profileBtn.addEventListener('click', () => {
      console.log('Profile menu clicked — hook up a dropdown here.');
    });
  }

  // --- Live "updated X min ago" ticker on the legend bar ---
  const updatedLabel = document.querySelector('.legend-item:last-child');
  if (updatedLabel) {
    let minutes = 2;
    setInterval(() => {
      minutes += 1;
      updatedLabel.textContent = `Updated ${minutes} min ago`;
    }, 60000);
  }
});
