        function openZAPIModal(locationId, companyName, instanceId, token, clientToken) {
            document.getElementById('zapi_location_id').value = locationId;
            document.getElementById('zapi_company_name').innerText = companyName;
            document.getElementById('zapi_instance_id').value = instanceId || '';
            document.getElementById('zapi_token').value = token || '';
            document.getElementById('zapi_client_token').value = clientToken || '';
            document.getElementById('form_zapi').action = '/admin/tenant/' + locationId + '/zapi';
            var host = window.location.host;
            document.getElementById('zapi_webhook_instruction').innerText = 'https://' + host + '/webhook/zapi/inbound/' + locationId;
            document.getElementById('zapi_webhook_status').innerText = 'https://' + host + '/webhook/zapi/status/' + locationId;
            document.getElementById('zapiModal').classList.remove('hidden');
        }

        function closeZAPIModal() {
            document.getElementById('zapiModal').classList.add('hidden');
        }

        function openNewCompanyModal() {
            document.getElementById('newCompanyModal').classList.remove('hidden');
        }

        function closeNewCompanyModal() {
            document.getElementById('newCompanyModal').classList.add('hidden');
        }

        function openWhatsAppOnlyModal() {
            closeNewCompanyModal();
            document.getElementById('whatsappOnlyModal').classList.remove('hidden');
        }

        function closeWhatsAppOnlyModal() {
            document.getElementById('whatsappOnlyModal').classList.add('hidden');
        }

        async function generateFormLink(locationId) {
            try {
                const resp = await fetch('/admin/tenant/' + locationId + '/generate-form-link', {
                    method: 'POST',
                });
                const data = await resp.json();
                if (data.success) {
                    await navigator.clipboard.writeText(data.url);
                    alert('Link copiado!\n\n' + data.url);
                } else {
                    alert('Erro ao gerar link: ' + (data.error || 'desconhecido'));
                }
            } catch (e) {
                alert('Erro de rede ao gerar link.');
            }
        }

        function openDeleteModal(locationId, companyName) {
            document.getElementById('delete_company_name').innerText = companyName;
            document.getElementById('form_delete_tenant').action = '/admin/tenant/' + locationId + '/delete';
            document.getElementById('deleteModal').classList.remove('hidden');
        }

        function closeDeleteModal() {
            document.getElementById('deleteModal').classList.add('hidden');
        }

        // ── Instance Settings Modal ──
        function openInstanceSettings(locationId, companyName, mode, clientId, zapiInstanceId, zapiToken, zapiClientToken, pitToken, telegramBotToken, telegramBotUsername) {
            document.getElementById('instance_settings_company').innerText = companyName;
            // Store data for sub-actions
            window._instanceSettingsData = { locationId, companyName, mode, clientId, zapiInstanceId, zapiToken, zapiClientToken, pitToken, telegramBotToken, telegramBotUsername };

            // Show reconnect/connect CRM for all tenants
            const reconnectCard = document.getElementById('instance_card_reconnect');
            if (reconnectCard) {
                // Sempre visível — whatsapp_only pode conectar CRM só para qualificação
                reconnectCard.classList.remove('hidden');
                const reconnectTitle = reconnectCard.querySelector('.instance-card-title');
                const reconnectDesc = reconnectCard.querySelector('.instance-card-desc');
                if (!clientId) {
                    if (reconnectTitle) reconnectTitle.textContent = 'Conectar CRM';
                    if (reconnectDesc) reconnectDesc.textContent = 'Conectar ao GHL para enviar leads qualificados';
                } else {
                    if (reconnectTitle) reconnectTitle.textContent = 'Reconectar CRM';
                    if (reconnectDesc) reconnectDesc.textContent = 'Re-autorizar OAuth do GHL (atualizar permissoes)';
                }
            }

            const pitTitle = document.getElementById('instance_card_pit_title');
            const pitDesc = document.getElementById('instance_card_pit_desc');
            if (pitTitle && pitDesc) {
                if (pitToken) {
                    pitTitle.textContent = 'Token PIT ✓';
                    pitDesc.textContent = 'Configurado — clique para atualizar';
                } else {
                    pitTitle.textContent = 'Token PIT';
                    pitDesc.textContent = 'Configurar Private Integration Token';
                }
            }

            const tgTitle = document.getElementById('instance_card_telegram_title');
            const tgDesc = document.getElementById('instance_card_telegram_desc');
            if (tgTitle && tgDesc) {
                if (telegramBotToken) {
                    tgTitle.textContent = 'Telegram Bot ✓';
                    tgDesc.textContent = telegramBotUsername ? '@' + telegramBotUsername + ' — clique para trocar' : 'Configurado — clique para trocar';
                } else {
                    tgTitle.textContent = 'Telegram Bot';
                    tgDesc.textContent = 'Conectar bot do Telegram (BotFather)';
                }
            }

            document.getElementById('instanceSettingsModal').classList.remove('hidden');
        }

        function closeInstanceSettings() {
            document.getElementById('instanceSettingsModal').classList.add('hidden');
        }

        function instanceAction(action) {
            const d = window._instanceSettingsData;
            closeInstanceSettings();
            if (action === 'agent') {
                // Find the row that has the data attributes and open AI modal
                const row = document.querySelector(`tr[data-location="${d.locationId}"]`);
                if (row) openAIAgentModal(row);
            } else if (action === 'zapi') {
                openZAPIModal(d.locationId, d.companyName, d.zapiInstanceId, d.zapiToken, d.zapiClientToken);
            } else if (action === 'reconnect') {
                if (d.clientId) {
                    let reconnectUrl = `/oauth/install?company=${encodeURIComponent(d.companyName)}&ci=${d.clientId}&ui_redirect=1`;
                    if (d.mode === 'whatsapp_only') reconnectUrl += `&existing=${encodeURIComponent(d.locationId)}`;
                    window.location.href = reconnectUrl;
                } else {
                    openConnectCRMModal(d.companyName, d.locationId);
                }
            } else if (action === 'pit') {
                openPitModal(d.companyName, d.locationId, d.pitToken);
            } else if (action === 'telegram') {
                openTelegramModal(d.companyName, d.locationId, d.telegramBotToken, d.telegramBotUsername);
            } else if (action === 'waha') {
                openWahaModal(d.locationId, d.companyName);
            } else if (action === 'form') {
                generateFormLink(d.locationId);
            } else if (action === 'delete') {
                openDeleteModal(d.locationId, d.companyName);
            }
        }

        function openConnectCRMModal(companyName, locationId) {
            document.getElementById('connect_crm_company').innerText = companyName;
            document.getElementById('connect_crm_company_hidden').value = companyName;
            document.getElementById('connect_crm_existing_id').value = locationId || '';
            document.getElementById('connect_crm_ci').value = '';
            document.getElementById('connect_crm_cs').value = '';
            document.getElementById('connectCRMModal').classList.remove('hidden');
        }

        function closeConnectCRMModal() {
            document.getElementById('connectCRMModal').classList.add('hidden');
        }

        function submitConnectCRM() {
            const company = document.getElementById('connect_crm_company_hidden').value;
            const existingId = document.getElementById('connect_crm_existing_id').value;
            const ci = document.getElementById('connect_crm_ci').value.trim();
            const cs = document.getElementById('connect_crm_cs').value.trim();
            if (!ci) { alert('Informe o Client ID do GHL.'); return; }
            let url = `/oauth/install?company=${encodeURIComponent(company)}&ci=${encodeURIComponent(ci)}&cs=${encodeURIComponent(cs)}&ui_redirect=1`;
            if (existingId) url += `&existing=${encodeURIComponent(existingId)}`;
            window.location.href = url;
        }

        function toggleCrmSection() {
            document.getElementById('crmSection').classList.toggle('hidden');
        }

        function toggleOnboardingSection() {
            document.getElementById('onboardingSection').classList.toggle('hidden');
        }

        function openOnboardingSuccessModal(url, company) {
            const linkInput = document.getElementById('onboardingSuccessLink');
            const companyLabel = document.getElementById('onboardingSuccessCompany');
            const feedback = document.getElementById('onboardingCopyFeedback');
            if (linkInput) linkInput.value = url || '';
            if (companyLabel) companyLabel.textContent = company || '';
            if (feedback) feedback.innerHTML = '&nbsp;';
            document.getElementById('onboardingSuccessModal').classList.remove('hidden');
        }

        function closeOnboardingSuccessModal() {
            document.getElementById('onboardingSuccessModal').classList.add('hidden');
        }

        async function copyOnboardingLink() {
            const linkInput = document.getElementById('onboardingSuccessLink');
            const feedback = document.getElementById('onboardingCopyFeedback');
            if (!linkInput || !linkInput.value) return;
            try {
                await navigator.clipboard.writeText(linkInput.value);
                if (feedback) feedback.textContent = 'Link copiado.';
            } catch (e) {
                linkInput.select();
                if (feedback) feedback.textContent = 'Selecione e copie manualmente.';
            }
        }

        // ── Onboarding review (dados preenchidos + gerar agente) ──
        const ONBOARDING_FIELD_LABELS = {
            company_name: 'Nome da empresa',
            industry: 'Segmento / Indústria',
            company_description: 'Descrição da empresa',
            target_audience: 'Público-alvo',
            website: 'Website',
            instagram: 'Instagram',
            products_services: 'Produtos / Serviços',
            differentials: 'Diferenciais',
            faq: 'FAQ',
            tone: 'Tom de voz',
            business_hours: 'Horário de atendimento',
            contact_info: 'Contato',
            agent_goal: 'Objetivo do agente',
            extra_info: 'Informações extras',
        };

        function closeOnboardingReviewModal() {
            document.getElementById('onboardingReviewModal').classList.add('hidden');
        }

        async function openOnboardingModal(locationId, company) {
            const modal = document.getElementById('onboardingReviewModal');
            const body = document.getElementById('onboardingReviewBody');
            document.getElementById('onboardingReviewCompany').textContent = company || locationId;
            body.innerHTML = '<p class="text-xs text-gray-500 font-mono">Carregando...</p>';
            modal.classList.remove('hidden');

            try {
                const resp = await fetch(`/admin/agents/onboarding/submissions?location_id=${encodeURIComponent(locationId)}`);
                const data = await resp.json();
                if (!data.success) {
                    body.innerHTML = `<p class="text-xs text-red-400 font-mono">${_escapeHtml(data.error || 'Erro ao carregar.')}</p>`;
                    return;
                }
                const subs = data.submissions || [];
                if (subs.length === 0) {
                    body.innerHTML = '<p class="text-xs text-gray-500 font-mono">Nenhuma submissão encontrada.</p>';
                    return;
                }
                body.innerHTML = subs.map(s => _renderSubmissionCard(s)).join('');
            } catch (e) {
                body.innerHTML = '<p class="text-xs text-red-400 font-mono">Erro de conexão.</p>';
            }
        }

        function _renderSubmissionCard(s) {
            const fd = s.form_data || {};
            const rows = Object.keys(ONBOARDING_FIELD_LABELS)
                .filter(k => fd[k] && String(fd[k]).trim())
                .map(k => `
                    <div class="border-b border-gray-800/60 py-2">
                        <p class="text-[9px] text-gray-500 font-mono uppercase tracking-widest">${_escapeHtml(ONBOARDING_FIELD_LABELS[k])}</p>
                        <p class="text-xs text-gray-200 font-mono whitespace-pre-wrap">${_escapeHtml(String(fd[k]))}</p>
                    </div>`).join('');

            const isProcessed = s.status === 'processed';
            const statusBadge = isProcessed
                ? '<span class="text-[9px] font-mono uppercase tracking-widest text-gray-400">Agente já gerado</span>'
                : '<span class="text-[9px] font-mono uppercase tracking-widest text-green-400">Pendente</span>';

            const actionBtn = isProcessed
                ? `<button disabled class="px-4 py-2 rounded-lg text-[10px] font-bold uppercase tracking-widest text-gray-600 bg-gray-800 cursor-not-allowed">Processado</button>`
                : `<button onclick="createAgentFromSubmission(${s.id}, this)"
                        class="px-4 py-2 rounded-lg text-[10px] font-bold uppercase tracking-widest text-white bg-green-600 hover:bg-green-500 transition-colors shadow-lg">
                        Gerar Agente
                    </button>`;

            return `
                <div class="bg-black/30 border border-gray-800 rounded-xl p-4" data-submission="${s.id}">
                    <div class="flex justify-between items-center mb-3">
                        <span class="text-[10px] text-gray-500 font-mono">#${s.id} · ${_escapeHtml(s.created_at || '')}</span>
                        ${statusBadge}
                    </div>
                    <div class="max-h-72 overflow-y-auto pr-1">${rows || '<p class="text-xs text-gray-600 font-mono">Formulário vazio.</p>'}</div>
                    <div class="flex justify-end mt-4">${actionBtn}</div>
                    <p class="onboarding-action-feedback text-[10px] font-mono text-right mt-2 h-4"></p>
                </div>`;
        }

        async function createAgentFromSubmission(submissionId, btn) {
            const card = btn.closest('[data-submission]');
            const feedback = card ? card.querySelector('.onboarding-action-feedback') : null;
            btn.disabled = true;
            const original = btn.textContent;
            btn.innerHTML = '<span class="animate-pulse">Gerando...</span>';
            if (feedback) { feedback.textContent = ''; feedback.classList.remove('text-red-400', 'text-green-400'); }
            try {
                const resp = await fetch(`/admin/agents/onboarding/submissions/${submissionId}/create-agent`, { method: 'POST' });
                const data = await resp.json();
                if (data.success) {
                    if (feedback) { feedback.textContent = 'Agente gerado! Recarregando...'; feedback.classList.add('text-green-400'); }
                    setTimeout(() => window.location.reload(), 1200);
                } else {
                    btn.disabled = false;
                    btn.textContent = original;
                    if (feedback) { feedback.textContent = data.error || 'Erro ao gerar.'; feedback.classList.add('text-red-400'); }
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = original;
                if (feedback) { feedback.textContent = 'Erro de conexão.'; feedback.classList.add('text-red-400'); }
            }
        }

        function switchCrmTab(tab) {
            const oauthTab = document.getElementById('crmTabOauth');
            const pitTab = document.getElementById('crmTabPit');
            const oauthForm = document.getElementById('ghlForm');
            const pitForm = document.getElementById('pitForm');

            if (tab === 'oauth') {
                oauthTab.className = 'flex-1 py-2 px-3 rounded-lg text-[10px] font-bold uppercase tracking-widest transition-all border border-green-500/50 text-green-400 bg-green-500/10';
                pitTab.className = 'flex-1 py-2 px-3 rounded-lg text-[10px] font-bold uppercase tracking-widest transition-all border border-gray-700 text-gray-500 hover:text-amber-400 hover:border-amber-500/50';
                oauthForm.classList.remove('hidden');
                pitForm.classList.add('hidden');
            } else {
                pitTab.className = 'flex-1 py-2 px-3 rounded-lg text-[10px] font-bold uppercase tracking-widest transition-all border border-amber-500/50 text-amber-400 bg-amber-500/10';
                oauthTab.className = 'flex-1 py-2 px-3 rounded-lg text-[10px] font-bold uppercase tracking-widest transition-all border border-gray-700 text-gray-500 hover:text-green-400 hover:border-green-500/50';
                pitForm.classList.remove('hidden');
                oauthForm.classList.add('hidden');
            }
        }

        async function saveFormData(_regenerateLegacy) {
            // Salva apenas os dados do formulário. A geração/melhoria do prompt
            // agora vive na aba Testador → "Aplicar Melhoria".
            const locationId = document.getElementById('ai_location_id').value;
            const channel = document.getElementById('ai_channel').value || 'whatsapp';
            const fields = document.querySelectorAll('#form_data_content [data-formkey]');
            const formData = {};
            fields.forEach(el => {
                if (el.type === 'radio') {
                    if (el.checked) formData[el.dataset.formkey] = el.value;
                } else {
                    formData[el.dataset.formkey] = el.value;
                }
            });

            try {
                const resp = await fetch(`/admin/agents/${locationId}/form-data`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ form_data: formData, regenerate: false, channel })
                });
                const data = await resp.json();
                if (data.success) {
                    alert('Dados salvos com sucesso!');
                } else {
                    alert('Erro: ' + (data.error || 'desconhecido'));
                }
            } catch (e) {
                alert('Erro de rede ao salvar.');
            }
        }

        function openTelegramModal(companyName, locationId, currentToken, currentUsername) {
            document.getElementById('telegram_modal_company').innerText = companyName;
            document.getElementById('telegramModalForm').action = `/admin/tenant/${locationId}/telegram`;
            document.getElementById('telegram_modal_token').value = '';
            const statusEl = document.getElementById('telegram_current_status');
            const usernameEl = document.getElementById('telegram_current_username');
            if (currentToken) {
                usernameEl.textContent = currentUsername ? '@' + currentUsername : '';
                statusEl.classList.remove('hidden');
            } else {
                statusEl.classList.add('hidden');
            }
            document.getElementById('telegram_test_result').classList.add('hidden');
            document.getElementById('telegram_test_btn_text').textContent = 'Testar Conexão';
            document.getElementById('telegramModal').classList.remove('hidden');
        }

        function closeTelegramModal() {
            document.getElementById('telegramModal').classList.add('hidden');
        }

        async function testTelegramConnection() {
            const token = document.getElementById('telegram_modal_token').value.trim();
            if (!token) { alert('Cole o token do bot primeiro.'); return; }

            const btnText = document.getElementById('telegram_test_btn_text');
            const resultEl = document.getElementById('telegram_test_result');
            btnText.textContent = 'Testando...';
            resultEl.classList.add('hidden');

            try {
                const resp = await fetch('/admin/test-telegram', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bot_token: token })
                });
                const data = await resp.json();
                resultEl.classList.remove('hidden');

                if (data.success) {
                    resultEl.className = 'rounded-lg p-3 text-xs font-mono bg-green-500/10 border border-green-500/30 text-green-400';
                    resultEl.innerHTML = `<div class="flex items-center gap-1.5 font-bold mb-1"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg> Bot encontrado</div>
                        <div class="text-gray-300">@${data.bot_username} — ${data.bot_name}</div>`;
                    btnText.textContent = 'Conexão OK ✓';
                } else {
                    resultEl.className = 'rounded-lg p-3 text-xs font-mono bg-red-500/10 border border-red-500/30 text-red-400';
                    resultEl.innerHTML = `${data.error}`;
                    btnText.textContent = 'Testar Conexão';
                }
            } catch (e) {
                resultEl.classList.remove('hidden');
                resultEl.className = 'rounded-lg p-3 text-xs font-mono bg-red-500/10 border border-red-500/30 text-red-400';
                resultEl.innerHTML = 'Erro de rede ao testar.';
                btnText.textContent = 'Testar Conexão';
            }
        }

        function openPitModal(companyName, locationId, currentPit) {
            document.getElementById('pit_modal_company').innerText = companyName;
            document.getElementById('pitModalForm').action = `/admin/tenant/${locationId}/pit`;
            document.getElementById('pit_modal_token').value = '';
            document.getElementById('pit_modal_ghl_loc').value = '';
            window._pitModalLocationId = locationId;
            const statusEl = document.getElementById('pit_current_status');
            if (currentPit) {
                statusEl.classList.remove('hidden');
            } else {
                statusEl.classList.add('hidden');
            }
            // Show GHL Location ID field for whatsapp_only tenants (wp_ prefix)
            const ghlLocGroup = document.getElementById('pit_ghl_loc_group');
            if (locationId.startsWith('wp_')) {
                ghlLocGroup.classList.remove('hidden');
            } else {
                ghlLocGroup.classList.add('hidden');
            }
            document.getElementById('pit_test_result').classList.add('hidden');
            document.getElementById('pit_test_btn_text').textContent = 'Testar Conexão';
            document.getElementById('pitModal').classList.remove('hidden');
        }

        function closePitModal() {
            document.getElementById('pitModal').classList.add('hidden');
        }

        async function testPitConnection() {
            const token = document.getElementById('pit_modal_token').value.trim();
            if (!token) { alert('Informe o token PIT primeiro.'); return; }

            const btnText = document.getElementById('pit_test_btn_text');
            const resultEl = document.getElementById('pit_test_result');
            btnText.textContent = 'Testando...';
            resultEl.classList.add('hidden');

            try {
                const resp = await fetch('/admin/test-pit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pit_token: token, location_id: document.getElementById('pit_modal_ghl_loc').value.trim() || window._pitModalLocationId || '' })
                });
                const data = await resp.json();
                resultEl.classList.remove('hidden');

                if (data.success) {
                    resultEl.className = 'rounded-lg p-3 text-xs font-mono bg-green-500/10 border border-green-500/30 text-green-400';
                    let html = '<div class="flex items-center gap-1.5 font-bold mb-1"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg> Conexão OK</div>';
                    if (data.location_name) html += `<div class="text-gray-300">Sub-account: <strong class="text-white">${data.location_name}</strong></div>`;
                    if (data.location_id) html += `<div class="text-gray-400">Location: ${data.location_id}</div>`;
                    resultEl.innerHTML = html;
                    btnText.textContent = 'Conexão OK ✓';
                } else {
                    resultEl.className = 'rounded-lg p-3 text-xs font-mono bg-red-500/10 border border-red-500/30 text-red-400';
                    resultEl.innerHTML = `<div class="flex items-center gap-1.5"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg> ${data.error}</div>`;
                    btnText.textContent = 'Testar Conexão';
                }
            } catch (e) {
                resultEl.classList.remove('hidden');
                resultEl.className = 'rounded-lg p-3 text-xs font-mono bg-red-500/10 border border-red-500/30 text-red-400';
                resultEl.innerHTML = 'Erro de rede ao testar.';
                btnText.textContent = 'Testar Conexão';
            }
        }

        function openAIAgentModal(btn) {
            const locationId = btn.dataset.location;
            const companyName = btn.dataset.company;
            const agentName = btn.dataset.ainame;
            const agentPrompt = btn.dataset.aiprompt;
            const agentModel = btn.dataset.aimodel;
            const agentApiKey = btn.dataset.aiapikey;
            const isActive = btn.dataset.aiisactive;
            const elevenlabsKey = btn.dataset.aielevenlabskey;
            const elevenlabsVoiceId = btn.dataset.aielevenlabsvoice;
            const groqKey = btn.dataset.aigroqkey;

            document.getElementById('ai_location_id').value = locationId;
            document.getElementById('ai_company_name').innerText = companyName;

            document.getElementById('ai_name').value = agentName || 'Agente IA';
            document.getElementById('ai_prompt').value = agentPrompt || 'Você é um assistente virtual prestativo.';
            document.getElementById('ai_model').value = agentModel || 'openai/gpt-4o';
            document.getElementById('ai_api_key').value = agentApiKey || '';
            document.getElementById('ai_is_active').checked = String(isActive).toLowerCase() === 'true';

            document.getElementById('ai_elevenlabs_api_key').value = elevenlabsKey || '';
            document.getElementById('ai_groq_api_key').value = groqKey || '';

            const speedVal = parseFloat(btn.dataset.aielspeed) || 1.0;
            const stabilityVal = parseFloat(btn.dataset.aielstability) || 0.5;
            const similarityVal = parseFloat(btn.dataset.aielsimilarity) || 0.75;
            document.getElementById('ai_elevenlabs_speed').value = speedVal;
            document.getElementById('el_speed_display').textContent = speedVal.toFixed(2) + 'x';
            document.getElementById('ai_elevenlabs_stability').value = stabilityVal;
            document.getElementById('el_stability_display').textContent = (stabilityVal * 100).toFixed(0) + '%';
            document.getElementById('ai_elevenlabs_similarity').value = similarityVal;
            document.getElementById('el_similarity_display').textContent = (similarityVal * 100).toFixed(0) + '%';

            const debounceVal = parseFloat(btn.dataset.aidebounce) || 1.5;
            const debounceInput = document.getElementById('ai_debounce_seconds');
            debounceInput.value = debounceVal;
            document.getElementById('debounce_display').textContent = debounceVal.toFixed(1) + 's';

            // Set voice if exists, otherwise load won't pre-select it perfectly until fetched, handled in fetch
            document.getElementById('ai_elevenlabs_voice_id').dataset.preselected = elevenlabsVoiceId || '';
            _restoreVoicePlaceholder();

            // TTS provider + Fish Audio
            const ttsProvider = btn.dataset.aittsprovider || 'elevenlabs';
            const ttsProviderEl = document.getElementById('ai_tts_provider');
            if (ttsProviderEl) ttsProviderEl.value = ttsProvider;
            const fishKeyEl = document.getElementById('ai_fishaudio_api_key');
            if (fishKeyEl) fishKeyEl.value = btn.dataset.aifishkey || '';
            const fishVoiceEl = document.getElementById('ai_fishaudio_voice_id');
            if (fishVoiceEl) fishVoiceEl.dataset.preselected = btn.dataset.aifishvoice || '';
            const fishModelEl = document.getElementById('ai_fishaudio_model');
            if (fishModelEl) fishModelEl.value = btn.dataset.aifishmodel || 's1';
            const fishSpeedVal = parseFloat(btn.dataset.aifishspeed) || 1.0;
            const fishSpeedEl = document.getElementById('ai_fishaudio_speed');
            if (fishSpeedEl) fishSpeedEl.value = fishSpeedVal;
            const fishSpeedDisp = document.getElementById('fish_speed_display');
            if (fishSpeedDisp) fishSpeedDisp.textContent = fishSpeedVal.toFixed(2) + 'x';

            const fishTempVal = parseFloat(btn.dataset.aifishtemp);
            const fishTempEl = document.getElementById('ai_fishaudio_temperature');
            const fishTempDisp = document.getElementById('fish_temp_display');
            const tempV = isNaN(fishTempVal) ? 0.7 : fishTempVal;
            if (fishTempEl) fishTempEl.value = tempV;
            if (fishTempDisp) fishTempDisp.textContent = tempV.toFixed(2);

            const fishTopPVal = parseFloat(btn.dataset.aifishtopp);
            const fishTopPEl = document.getElementById('ai_fishaudio_top_p');
            const fishTopPDisp = document.getElementById('fish_topp_display');
            const topPV = isNaN(fishTopPVal) ? 0.7 : fishTopPVal;
            if (fishTopPEl) fishTopPEl.value = topPV;
            if (fishTopPDisp) fishTopPDisp.textContent = topPV.toFixed(2);

            const fishNormEl = document.getElementById('ai_fishaudio_normalize_loudness');
            if (fishNormEl) fishNormEl.checked = String(btn.dataset.aifishnormloud).toLowerCase() === 'true';

            _restoreFishVoicePlaceholder();
            toggleTtsProviderBlocks();

            // Qualification tab
            const qualAtivard = String(btn.dataset.aiqualenabled).toLowerCase() === 'true';
            document.getElementById('ai_qual_enabled').checked = qualAtivard;
            document.getElementById('ai_qual_summary_prompt').value = btn.dataset.aiqualsummaryprompt || '';

            // Store saved pipeline/stage IDs for restoration after fetch
            window._qualSavedPipelineId = btn.dataset.aiqualpipelineid || '';
            window._qualSavedStageId = btn.dataset.aiqualstageid || '';
            window._qualPipelinesData = [];
            window._qualCustomFieldsData = [];

            // Restore pipeline/stage dropdowns
            const pipelineSelect = document.getElementById('ai_qual_pipeline');
            pipelineSelect.innerHTML = '<option value="">-- Select Pipeline --</option>';
            if (window._qualSavedPipelineId) {
                pipelineSelect.innerHTML += `<option value="${window._qualSavedPipelineId}" selected>${window._qualSavedPipelineId}</option>`;
            }
            const stageSelect = document.getElementById('ai_qual_stage');
            stageSelect.innerHTML = '<option value="">-- Select a pipeline first --</option>';
            if (window._qualSavedStageId) {
                stageSelect.innerHTML += `<option value="${window._qualSavedStageId}" selected>${window._qualSavedStageId}</option>`;
            }

            // Restore fields
            let savedFields = [];
            try { savedFields = JSON.parse(btn.dataset.aiqualfields || '[]'); } catch(e) {}
            const container = document.getElementById('qual_fields_container');
            container.innerHTML = '';
            if (savedFields.length > 0) {
                savedFields.forEach(f => addQualField(f.label, f.key, f.ghl_field_id, f.auto || false));
                // Set _qualFields for history tab tooltip
                _qualFields = savedFields;
            } else {
                _updateFieldsVisibility();
            }
            serializeQualFields();

            // Form Data tab (Cadastro)
            const formDataRaw = btn.dataset.aiformdata || '';
            const formEmpty = document.getElementById('form_data_empty');
            const formContent = document.getElementById('form_data_content');
            const formActions = document.getElementById('form_data_actions');
            if (formDataRaw) {
                try {
                    const fd = JSON.parse(formDataRaw);
                    formEmpty.classList.add('hidden');
                    formContent.classList.remove('hidden');
                    formActions.classList.remove('hidden');
                    const fieldLabels = {
                        company_name: 'Empresa', industry: 'Segmento', company_description: 'Descrição',
                        target_audience: 'Público-alvo', website: 'Website', instagram: 'Instagram',
                        products_services: 'Produtos/Serviços', differentials: 'Diferenciais', faq: 'FAQ',
                        agent_name: 'Nome do Agente', tone: 'Tom de Voz', business_hours: 'Horário',
                        contact_info: 'Contatos', agent_goal: 'Objetivo', restrictions: 'Restrições',
                        qualification_questions: 'Perguntas Qualificatórias',
                        extra_info: 'Info Adicional'
                    };
                    const shortFields = ['company_name', 'industry', 'target_audience', 'website', 'instagram', 'agent_name', 'tone', 'business_hours'];
                    const agentType = fd.agent_type || 'inbound';
                    const toneRegister = fd.tone_register || '';
                    let html = '';
                    html += `<div>
                        <label class="block text-[10px] font-bold text-gray-500 uppercase tracking-widest font-mono mb-2">Tipo de Atendimento</label>
                        <div class="grid grid-cols-2 gap-2">
                            <label class="cursor-pointer">
                                <input type="radio" name="form_agent_type" value="inbound" ${agentType==='inbound'?'checked':''} class="hidden peer" data-formkey="agent_type">
                                <div class="peer-checked:border-brand-red peer-checked:bg-brand-red/5 border border-gray-700 rounded-lg p-3 transition-all">
                                    <p class="text-sm font-bold text-white">📥 Inbound</p>
                                    <p class="text-[10px] text-gray-400 mt-0.5">Passivo — responde clientes</p>
                                </div>
                            </label>
                            <label class="cursor-pointer">
                                <input type="radio" name="form_agent_type" value="outbound" ${agentType==='outbound'?'checked':''} class="hidden peer" data-formkey="agent_type">
                                <div class="peer-checked:border-brand-red peer-checked:bg-brand-red/5 border border-gray-700 rounded-lg p-3 transition-all">
                                    <p class="text-sm font-bold text-white">📤 Outbound</p>
                                    <p class="text-[10px] text-gray-400 mt-0.5">Ativo — inicia contato</p>
                                </div>
                            </label>
                        </div>
                    </div>`;
                    html += `<div>
                        <label class="block text-[10px] font-bold text-gray-500 uppercase tracking-widest font-mono mb-1">Registro de Linguagem (opcional)</label>
                        <select data-formkey="tone_register" class="input-dark w-full py-2 px-3 rounded-lg text-sm">
                            <option value="" ${toneRegister===''?'selected':''}>Auto (detecta pelo segmento)</option>
                            <option value="premium" ${toneRegister==='premium'?'selected':''}>Premium — B2B executivo, sem gírias</option>
                            <option value="neutro" ${toneRegister==='neutro'?'selected':''}>Neutro — profissional descontraído</option>
                            <option value="casual" ${toneRegister==='casual'?'selected':''}>Casual — B2C, tom amigável</option>
                            <option value="support" ${toneRegister==='support'?'selected':''}>Suporte — técnico empático</option>
                        </select>
                        <p class="text-[10px] text-gray-600 mt-1">Use Auto na maioria dos casos. Force só se a detecção automática errar.</p>
                    </div>`;
                    for (const [key, label] of Object.entries(fieldLabels)) {
                        const val = fd[key] || '';
                        const escaped = val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
                        if (shortFields.includes(key)) {
                            html += `<div>
                                <label class="block text-[10px] font-bold text-gray-500 uppercase tracking-widest font-mono mb-1">${label}</label>
                                <input type="text" data-formkey="${key}" value="${escaped}" class="input-dark w-full py-2 px-3 rounded-lg text-sm">
                            </div>`;
                        } else {
                            html += `<div>
                                <label class="block text-[10px] font-bold text-gray-500 uppercase tracking-widest font-mono mb-1">${label}</label>
                                <textarea data-formkey="${key}" class="input-dark w-full py-2 px-3 rounded-lg text-sm" rows="3">${escaped}</textarea>
                            </div>`;
                        }
                    }
                    formContent.innerHTML = html;
                } catch(e) {
                    formEmpty.classList.remove('hidden');
                    formContent.classList.add('hidden');
                    formActions.classList.add('hidden');
                }
            } else {
                formEmpty.classList.remove('hidden');
                formContent.classList.add('hidden');
                formActions.classList.add('hidden');
            }

            document.getElementById('form_ai_agent').action = '/admin/agents/' + locationId + '/save';
            document.getElementById('ai_channel').value = 'whatsapp';
            _toggleInheritKeysButton('whatsapp');
            if (typeof _hideLinkedBanner === 'function') _hideLinkedBanner();
            document.getElementById('aiAgentModal').classList.remove('hidden');

            // Default to Settings Tab
            switchAITab('settings');

            // Carregar canais disponiveis para esse tenant
            loadChannelsForTenant(locationId);
        }

        // ── Multi-canal: gerenciamento de agentes por canal ──
        const CHANNEL_LABELS = {
            whatsapp: { label: 'WhatsApp', icon: '💬' },
            instagram: { label: 'Instagram', icon: '📸' },
            facebook: { label: 'Facebook', icon: '👍' },
            telegram: { label: 'Telegram', icon: '✈️' },
        };

        async function loadChannelsForTenant(locationId) {
            const container = document.getElementById('channel_tabs_container');
            container.innerHTML = '<span class="text-[10px] text-gray-600 font-mono">carregando...</span>';
            try {
                const resp = await fetch(`/admin/agents/${locationId}/list`);
                const data = await resp.json();
                if (!data.success) {
                    container.innerHTML = '<span class="text-[10px] text-red-400 font-mono">erro</span>';
                    return;
                }
                const channels = data.agents.map(a => a.channel);
                if (!channels.includes('whatsapp')) channels.unshift('whatsapp');
                _renderChannelTabs(channels, 'whatsapp');
            } catch(e) {
                container.innerHTML = '<span class="text-[10px] text-red-400 font-mono">erro de rede</span>';
            }
        }

        function _renderChannelTabs(channels, activeChannel) {
            const container = document.getElementById('channel_tabs_container');
            container.innerHTML = channels.map(ch => {
                const meta = CHANNEL_LABELS[ch] || { label: ch, icon: '🔌' };
                const isActive = ch === activeChannel;
                const closeBtn = ch !== 'whatsapp'
                    ? `<span onclick="event.stopPropagation();deleteChannel('${ch}')" class="ml-1 text-gray-600 hover:text-red-400 cursor-pointer" title="Remover canal">×</span>`
                    : '';
                return `<button type="button" onclick="switchChannel('${ch}')"
                    class="channel-tab flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider font-mono transition-colors ${isActive ? 'bg-brand-red/20 text-brand-red border border-brand-red/40' : 'text-gray-500 hover:text-white border border-transparent hover:border-gray-700'}">
                    <span>${meta.icon}</span>
                    <span>${meta.label}</span>
                    ${closeBtn}
                </button>`;
            }).join('');
        }

        function _toggleInheritKeysButton(channel) {
            const btn = document.getElementById('btn_inherit_keys');
            if (!btn) return;
            // Só mostra o "Usar chaves do WhatsApp" em canais que NÃO sejam o whatsapp
            if (channel && channel !== 'whatsapp') {
                btn.classList.remove('hidden');
            } else {
                btn.classList.add('hidden');
            }
        }

        async function inheritKeysFromWhatsApp() {
            const locationId = document.getElementById('ai_location_id').value;
            if (!locationId) return;
            try {
                const resp = await fetch(`/admin/agents/${locationId}/inherit-keys`);
                const data = await resp.json();
                if (!data.success) {
                    alert(data.error || 'Erro ao buscar chaves do WhatsApp.');
                    return;
                }
                if (data.api_key) document.getElementById('ai_api_key').value = data.api_key;
                if (data.model) document.getElementById('ai_model').value = data.model;
                if (data.groq_api_key) document.getElementById('ai_groq_api_key').value = data.groq_api_key;
                if (data.elevenlabs_api_key) document.getElementById('ai_elevenlabs_api_key').value = data.elevenlabs_api_key;
                if (data.elevenlabs_voice_id) document.getElementById('ai_elevenlabs_voice_id').dataset.preselected = data.elevenlabs_voice_id;
            } catch(e) {
                alert('Erro de rede ao buscar chaves.');
            }
        }

        function _showLinkedBanner(channel, linkedTo) {
            // Esconde o form completo de config e mostra um aviso "vinculado a X"
            const form = document.getElementById('form_ai_agent');
            let banner = document.getElementById('linked_channel_banner');
            if (!banner) {
                banner = document.createElement('div');
                banner.id = 'linked_channel_banner';
                banner.className = 'bg-sky-500/10 border border-sky-500/30 rounded-xl p-6 text-center';
                form.parentNode.insertBefore(banner, form);
            }
            const meta = CHANNEL_LABELS[linkedTo] || { label: linkedTo, icon: '🔌' };
            banner.innerHTML = `
                <div class="flex items-center justify-center gap-3 mb-3">
                    <svg class="w-6 h-6 text-sky-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
                    <p class="text-white font-bold uppercase tracking-wider">Canal Vinculado</p>
                </div>
                <p class="text-sm text-gray-300 mb-2">Este canal usa as configurações do agente <span class="text-white font-bold">${meta.label}</span>.</p>
                <p class="text-xs text-gray-500 mb-5">Edite o agente ${meta.label} para alterar prompt, qualificação ou chaves de ambos.</p>
                <div class="flex gap-3 justify-center">
                    <button type="button" onclick="switchChannel('${linkedTo}')"
                        class="px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-widest bg-sky-600 hover:bg-sky-500 text-white transition-colors">
                        Ir para ${meta.label}
                    </button>
                    <button type="button" onclick="unlinkChannel('${channel}')"
                        class="px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-widest text-gray-400 hover:text-white border border-gray-700 transition-colors">
                        Desvincular
                    </button>
                </div>`;
            banner.classList.remove('hidden');
            form.classList.add('hidden');
        }

        function _hideLinkedBanner() {
            const banner = document.getElementById('linked_channel_banner');
            if (banner) banner.classList.add('hidden');
            document.getElementById('form_ai_agent').classList.remove('hidden');
        }

        async function unlinkChannel(channel) {
            if (!confirm(`Desvincular o canal ${channel}? Vai virar um agente independente vazio.`)) return;
            const locationId = document.getElementById('ai_location_id').value;
            try {
                const resp = await fetch(`/admin/agents/${locationId}/unlink-channel`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ channel }),
                });
                const data = await resp.json();
                if (data.success) await switchChannel(channel);
                else alert('Erro: ' + (data.error || 'desconhecido'));
            } catch(e) { alert('Erro de rede.'); }
        }

        async function switchChannel(channel) {
            const locationId = document.getElementById('ai_location_id').value;
            if (!locationId) return;
            try {
                const resp = await fetch(`/admin/agents/${locationId}/agent?channel=${encodeURIComponent(channel)}`);
                const data = await resp.json();
                if (!data.success) {
                    alert('Erro ao carregar agente: ' + (data.error || 'desconhecido'));
                    return;
                }
                document.getElementById('ai_channel').value = channel;

                // Se o canal é alias, mostra banner em vez do form
                const linkedTo = data.agent && data.agent.linked_to_channel;
                if (linkedTo) {
                    _showLinkedBanner(channel, linkedTo);
                    const tabs = document.querySelectorAll('#channel_tabs_container button');
                    const channels = Array.from(tabs).map(b => {
                        const m = b.getAttribute('onclick').match(/switchChannel\('([^']+)'\)/);
                        return m ? m[1] : null;
                    }).filter(Boolean);
                    _renderChannelTabs(channels, channel);
                    return;
                }
                _hideLinkedBanner();
                _populateAgentForm(data.agent || {});
                _toggleInheritKeysButton(channel);
                // Re-render tabs com o novo canal ativo
                const tabs = document.querySelectorAll('#channel_tabs_container button');
                const channels = Array.from(tabs).map(b => {
                    const m = b.getAttribute('onclick').match(/switchChannel\('([^']+)'\)/);
                    return m ? m[1] : null;
                }).filter(Boolean);
                _renderChannelTabs(channels, channel);
            } catch(e) {
                alert('Erro de rede ao trocar canal.');
            }
        }

        function _populateAgentForm(agent) {
            // Preenche o form com os dados do agente do canal selecionado
            document.getElementById('ai_name').value = agent.name || 'Agente IA';
            document.getElementById('ai_prompt').value = agent.prompt || 'Voce e um assistente virtual prestativo.';
            document.getElementById('ai_model').value = agent.model || 'openai/gpt-4o';
            document.getElementById('ai_api_key').value = agent.api_key || '';
            document.getElementById('ai_is_active').checked = !!agent.is_active;
            document.getElementById('ai_elevenlabs_api_key').value = agent.elevenlabs_api_key || '';
            document.getElementById('ai_groq_api_key').value = agent.groq_api_key || '';
            document.getElementById('ai_elevenlabs_voice_id').dataset.preselected = agent.elevenlabs_voice_id || '';
            _restoreVoicePlaceholder();

            const speedVal = agent.elevenlabs_speed || 1.0;
            const stabilityVal = agent.elevenlabs_stability || 0.5;
            const similarityVal = agent.elevenlabs_similarity || 0.75;
            document.getElementById('ai_elevenlabs_speed').value = speedVal;
            document.getElementById('el_speed_display').textContent = speedVal.toFixed(2) + 'x';
            document.getElementById('ai_elevenlabs_stability').value = stabilityVal;
            document.getElementById('el_stability_display').textContent = (stabilityVal * 100).toFixed(0) + '%';
            document.getElementById('ai_elevenlabs_similarity').value = similarityVal;
            document.getElementById('el_similarity_display').textContent = (similarityVal * 100).toFixed(0) + '%';

            // Fish Audio
            const provider = agent.tts_provider || 'elevenlabs';
            const ttsProviderEl = document.getElementById('ai_tts_provider');
            if (ttsProviderEl) ttsProviderEl.value = provider;
            const fishKeyEl = document.getElementById('ai_fishaudio_api_key');
            if (fishKeyEl) fishKeyEl.value = agent.fishaudio_api_key || '';
            const fishVoiceEl = document.getElementById('ai_fishaudio_voice_id');
            if (fishVoiceEl) fishVoiceEl.dataset.preselected = agent.fishaudio_voice_id || '';
            const fishModelEl = document.getElementById('ai_fishaudio_model');
            if (fishModelEl) fishModelEl.value = agent.fishaudio_model || 's1';
            const fishSpeedVal = agent.fishaudio_speed || 1.0;
            const fishSpeedEl = document.getElementById('ai_fishaudio_speed');
            if (fishSpeedEl) fishSpeedEl.value = fishSpeedVal;
            const fishSpeedDisp = document.getElementById('fish_speed_display');
            if (fishSpeedDisp) fishSpeedDisp.textContent = fishSpeedVal.toFixed(2) + 'x';

            const tempV = (agent.fishaudio_temperature !== undefined && agent.fishaudio_temperature !== null) ? agent.fishaudio_temperature : 0.7;
            const fishTempEl = document.getElementById('ai_fishaudio_temperature');
            const fishTempDisp = document.getElementById('fish_temp_display');
            if (fishTempEl) fishTempEl.value = tempV;
            if (fishTempDisp) fishTempDisp.textContent = tempV.toFixed(2);

            const topPV = (agent.fishaudio_top_p !== undefined && agent.fishaudio_top_p !== null) ? agent.fishaudio_top_p : 0.7;
            const fishTopPEl = document.getElementById('ai_fishaudio_top_p');
            const fishTopPDisp = document.getElementById('fish_topp_display');
            if (fishTopPEl) fishTopPEl.value = topPV;
            if (fishTopPDisp) fishTopPDisp.textContent = topPV.toFixed(2);

            const fishNormEl = document.getElementById('ai_fishaudio_normalize_loudness');
            if (fishNormEl) fishNormEl.checked = !!agent.fishaudio_normalize_loudness;

            _restoreFishVoicePlaceholder();
            toggleTtsProviderBlocks();

            const debounceVal = agent.debounce_seconds || 1.5;
            document.getElementById('ai_debounce_seconds').value = debounceVal;
            document.getElementById('debounce_display').textContent = debounceVal.toFixed(1) + 's';

            document.getElementById('ai_qual_enabled').checked = !!agent.qualification_enabled;
            document.getElementById('ai_qual_summary_prompt').value = agent.qualification_summary_prompt || '';
            window._qualSavedPipelineId = agent.qualification_pipeline_id || '';
            window._qualSavedStageId = agent.qualification_stage_id || '';

            const container = document.getElementById('qual_fields_container');
            container.innerHTML = '';
            const savedFields = agent.qualification_fields || [];
            if (savedFields.length > 0) {
                savedFields.forEach(f => addQualField(f.label, f.key, f.ghl_field_id, f.auto || false));
                _qualFields = savedFields;
            } else {
                _updateFieldsVisibility();
            }
            serializeQualFields();

            // Atualiza a aba Cadastro (form_data) com os dados do canal corrente
            const fdRaw = agent.form_data ? JSON.stringify(agent.form_data) : '';
            const row = document.querySelector(`tr[data-location="${document.getElementById('ai_location_id').value}"]`);
            if (row) row.dataset.aiformdata = fdRaw;
            _renderFormDataTab(fdRaw);
        }

        function _renderFormDataTab(formDataRaw) {
            const formEmpty = document.getElementById('form_data_empty');
            const formContent = document.getElementById('form_data_content');
            const formActions = document.getElementById('form_data_actions');
            if (!formDataRaw) {
                formEmpty.classList.remove('hidden');
                formContent.classList.add('hidden');
                formActions.classList.add('hidden');
                return;
            }
            try {
                const fd = JSON.parse(formDataRaw);
                formEmpty.classList.add('hidden');
                formContent.classList.remove('hidden');
                formActions.classList.remove('hidden');
                const fieldLabels = {
                    company_name: 'Empresa', industry: 'Segmento', company_description: 'Descrição',
                    target_audience: 'Público-alvo', website: 'Website', instagram: 'Instagram',
                    products_services: 'Produtos/Serviços', differentials: 'Diferenciais', faq: 'FAQ',
                    agent_name: 'Nome do Agente', tone: 'Tom de Voz', business_hours: 'Horário',
                    contact_info: 'Contatos', agent_goal: 'Objetivo', restrictions: 'Restrições',
                    qualification_questions: 'Perguntas Qualificatórias',
                    extra_info: 'Info Adicional'
                };
                const shortFields = ['company_name', 'industry', 'target_audience', 'website', 'instagram', 'agent_name', 'tone', 'business_hours'];
                const agentType = fd.agent_type || 'inbound';
                const toneRegister = fd.tone_register || '';
                let html = `<div>
                    <label class="block text-[10px] font-bold text-gray-500 uppercase tracking-widest font-mono mb-2">Tipo de Atendimento</label>
                    <div class="grid grid-cols-2 gap-2">
                        <label class="cursor-pointer">
                            <input type="radio" name="form_agent_type" value="inbound" ${agentType==='inbound'?'checked':''} class="hidden peer" data-formkey="agent_type">
                            <div class="peer-checked:border-brand-red peer-checked:bg-brand-red/5 border border-gray-700 rounded-lg p-3 transition-all">
                                <p class="text-sm font-bold text-white">Inbound</p>
                                <p class="text-[10px] text-gray-400 mt-0.5">Passivo — responde clientes</p>
                            </div>
                        </label>
                        <label class="cursor-pointer">
                            <input type="radio" name="form_agent_type" value="outbound" ${agentType==='outbound'?'checked':''} class="hidden peer" data-formkey="agent_type">
                            <div class="peer-checked:border-brand-red peer-checked:bg-brand-red/5 border border-gray-700 rounded-lg p-3 transition-all">
                                <p class="text-sm font-bold text-white">Outbound</p>
                                <p class="text-[10px] text-gray-400 mt-0.5">Ativo — inicia contato</p>
                            </div>
                        </label>
                    </div>
                </div>`;
                html += `<div>
                    <label class="block text-[10px] font-bold text-gray-500 uppercase tracking-widest font-mono mb-1">Registro de Linguagem (opcional)</label>
                    <select data-formkey="tone_register" class="input-dark w-full py-2 px-3 rounded-lg text-sm">
                        <option value="" ${toneRegister===''?'selected':''}>Auto (detecta pelo segmento)</option>
                        <option value="premium" ${toneRegister==='premium'?'selected':''}>Premium — B2B executivo, sem gírias</option>
                        <option value="neutro" ${toneRegister==='neutro'?'selected':''}>Neutro — profissional descontraído</option>
                        <option value="casual" ${toneRegister==='casual'?'selected':''}>Casual — B2C, tom amigável</option>
                        <option value="support" ${toneRegister==='support'?'selected':''}>Suporte — técnico empático</option>
                    </select>
                </div>`;
                for (const [key, label] of Object.entries(fieldLabels)) {
                    const val = fd[key] || '';
                    const escaped = val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
                    if (shortFields.includes(key)) {
                        html += `<div><label class="block text-[10px] font-bold text-gray-500 uppercase tracking-widest font-mono mb-1">${label}</label><input type="text" data-formkey="${key}" value="${escaped}" class="input-dark w-full py-2 px-3 rounded-lg text-sm"></div>`;
                    } else {
                        html += `<div><label class="block text-[10px] font-bold text-gray-500 uppercase tracking-widest font-mono mb-1">${label}</label><textarea data-formkey="${key}" class="input-dark w-full py-2 px-3 rounded-lg text-sm" rows="3">${escaped}</textarea></div>`;
                    }
                }
                formContent.innerHTML = html;
            } catch(e) {
                formEmpty.classList.remove('hidden');
                formContent.classList.add('hidden');
                formActions.classList.add('hidden');
            }
        }

        async function openAddChannelModal() {
            const channels = Object.keys(CHANNEL_LABELS).filter(c => c !== 'whatsapp');
            const choice = prompt('Qual canal voce quer adicionar?\n\nOpcoes: ' + channels.join(', '));
            if (!choice) return;
            const normalized = choice.trim().toLowerCase();
            if (!CHANNEL_LABELS[normalized]) {
                alert('Canal invalido. Use: ' + channels.join(', '));
                return;
            }

            // Pergunta: vincular ao agente existente ou criar separado?
            const linkChoice = confirm(
                `Adicionar canal ${CHANNEL_LABELS[normalized].label}.\n\n` +
                `OK = Vincular ao agente WhatsApp (mesmo prompt, chaves, qualificação)\n` +
                `Cancelar = Criar agente separado (config independente)`
            );

            const locationId = document.getElementById('ai_location_id').value;

            if (linkChoice) {
                // Cria como alias do canal whatsapp
                try {
                    const resp = await fetch(`/admin/agents/${locationId}/link-channel`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ channel: normalized, linked_to: 'whatsapp' }),
                    });
                    const data = await resp.json();
                    if (!data.success) {
                        alert('Erro ao vincular: ' + (data.error || 'desconhecido'));
                        return;
                    }
                } catch(e) {
                    alert('Erro de rede ao vincular canal.');
                    return;
                }
                // Adiciona aba e troca pra ela (vai mostrar a tela de "vinculado")
                const tabs = document.querySelectorAll('#channel_tabs_container button');
                const existingChannels = Array.from(tabs).map(b => {
                    const m = b.getAttribute('onclick').match(/switchChannel\('([^']+)'\)/);
                    return m ? m[1] : null;
                }).filter(Boolean);
                if (!existingChannels.includes(normalized)) existingChannels.push(normalized);
                _renderChannelTabs(existingChannels, normalized);
                await switchChannel(normalized);
                return;
            }

            // Caminho ANTIGO: agente independente
            const row = document.querySelector(`tr[data-location="${locationId}"]`);
            const baseFormData = (row && row.dataset.aiformdata) ? row.dataset.aiformdata : '';
            document.getElementById('ai_channel').value = normalized;
            _populateAgentForm({ form_data: baseFormData ? JSON.parse(baseFormData) : null });
            _toggleInheritKeysButton(normalized);
            await inheritKeysFromWhatsApp();
            const tabs = document.querySelectorAll('#channel_tabs_container button');
            const existingChannels = Array.from(tabs).map(b => {
                const m = b.getAttribute('onclick').match(/switchChannel\('([^']+)'\)/);
                return m ? m[1] : null;
            }).filter(Boolean);
            if (!existingChannels.includes(normalized)) existingChannels.push(normalized);
            _renderChannelTabs(existingChannels, normalized);
        }

        async function deleteChannel(channel) {
            if (channel === 'whatsapp') return;
            if (!confirm(`Remover o agente do canal ${channel}? Essa acao nao pode ser desfeita.`)) return;
            const locationId = document.getElementById('ai_location_id').value;
            try {
                const resp = await fetch(`/admin/agents/${locationId}/agent?channel=${encodeURIComponent(channel)}`, { method: 'DELETE' });
                const data = await resp.json();
                if (data.success) {
                    await loadChannelsForTenant(locationId);
                    await switchChannel('whatsapp');
                } else {
                    alert('Erro ao remover: ' + (data.error || 'desconhecido'));
                }
            } catch(e) {
                alert('Erro de rede ao remover canal.');
            }
        }

        // ── Qualification Tab Functions ──

        async function loadQualPipelines() {
            const locationId = document.getElementById('ai_location_id').value;
            if (!locationId) return;
            const select = document.getElementById('ai_qual_pipeline');
            select.innerHTML = '<option value="">Loading...</option>';
            try {
                const resp = await fetch(`/admin/agents/${locationId}/ghl/pipelines`);
                const data = await resp.json();
                if (data.success && data.pipelines) {
                    window._qualPipelinesData = data.pipelines;
                    select.innerHTML = '<option value="">-- Select Pipeline --</option>';
                    data.pipelines.forEach(p => {
                        const selected = p.id === window._qualSavedPipelineId ? 'selected' : '';
                        select.innerHTML += `<option value="${p.id}" ${selected}>${p.name}</option>`;
                    });
                    if (select.value) onQualPipelineChange();
                } else {
                    const errMsg = data.error || 'Falha ao carregar';
                    select.innerHTML = `<option value="">${errMsg}</option>`;
                    alert('Pipelines: ' + errMsg);
                }
            } catch(e) {
                select.innerHTML = '<option value="">Error loading pipelines</option>';
            }
        }

        function onQualPipelineChange() {
            const pipelineId = document.getElementById('ai_qual_pipeline').value;
            const stageSelect = document.getElementById('ai_qual_stage');
            stageSelect.innerHTML = '<option value="">-- Select Stage --</option>';
            if (!pipelineId) return;

            const pipeline = (window._qualPipelinesData || []).find(p => p.id === pipelineId);
            if (pipeline && pipeline.stages) {
                pipeline.stages.forEach(s => {
                    const selected = s.id === window._qualSavedStageId ? 'selected' : '';
                    stageSelect.innerHTML += `<option value="${s.id}" ${selected}>${s.name}</option>`;
                });
            }
        }

        async function loadQualCustomFields() {
            const locationId = document.getElementById('ai_location_id').value;
            if (!locationId) return;

            const btn = document.getElementById('btn_fetch_ghl_fields');
            const statusEl = document.getElementById('qual_ghl_fields_status');
            if (btn) { btn.textContent = 'Buscando...'; btn.disabled = true; }
            if (statusEl) statusEl.textContent = '';

            try {
                const resp = await fetch(`/admin/agents/${locationId}/ghl/custom-fields`);
                const data = await resp.json();
                if (data.success && data.fields) {
                    window._qualCustomFieldsData = data.fields;
                    if (btn) btn.textContent = `${data.fields.length} campos carregados`;
                    if (statusEl) statusEl.textContent = `(${data.fields.length})`;
                    _refreshGhlDropdowns();
                } else {
                    if (btn) btn.textContent = 'Erro ao buscar';
                    if (statusEl) statusEl.textContent = '(erro)';
                    console.warn('Campos GHL:', data.error);
                }
            } catch(e) {
                if (btn) btn.textContent = 'Sem conexao CRM';
                if (statusEl) statusEl.textContent = '(erro)';
                console.error('Erro ao carregar campos:', e);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    setTimeout(() => { btn.textContent = 'Buscar Campos GHL'; }, 3000);
                }
            }
        }

        function _refreshGhlDropdowns() {
            const fields = window._qualCustomFieldsData || [];
            document.querySelectorAll('.qual-ghl-field-select').forEach(sel => {
                // Prefer data-ghl-field (saved value) over current select value
                const savedVal = sel.dataset.ghlField || sel.value || '';
                sel.innerHTML = '<option value="">-- Nao mapear --</option>';
                fields.forEach(f => {
                    const selected = f.id === savedVal ? 'selected' : '';
                    sel.innerHTML += `<option value="${f.id}" ${selected}>${_ghlFieldLabel(f)}</option>`;
                });
                // Clear data attribute after restoring
                if (sel.dataset.ghlField) delete sel.dataset.ghlField;
            });
            serializeQualFields();
        }

        function _ghlFieldLabel(f) {
            const tags = {
                'contact_std':      '[Contato]',
                'opportunity_std':  '[Opp]',
                'contact':          '[Contato Custom]',
                'opportunity':      '[Opp Custom]',
            };
            const tag = tags[f._model] || '';
            return tag ? `${tag} ${f.name}` : f.name;
        }

        function _ghlFieldOptions(selectedId) {
            const fields = window._qualCustomFieldsData || [];
            return fields.map(f =>
                `<option value="${f.id}" ${f.id === selectedId ? 'selected' : ''}>${_ghlFieldLabel(f)}</option>`
            ).join('');
        }

        function addQualField(label, key, ghlFieldId, auto) {
            label = label || '';
            key = key || '';
            ghlFieldId = ghlFieldId || '';
            auto = auto || false;
            const container = document.getElementById('qual_fields_container');

            const row = document.createElement('div');
            row.className = 'grid grid-cols-12 gap-2 items-center bg-black/30 border border-gray-800 rounded-lg p-2.5';
            row.innerHTML = `
                <input type="text" placeholder="${auto ? 'ex: Classificacao do Lead' : 'ex: Nome Completo'}" value="${label}"
                    class="qual-field-label input-dark col-span-3 px-3 py-2 rounded-lg text-xs font-mono ${auto ? 'border-brand-red/30' : ''}" onchange="autoGenerateKey(this); serializeQualFields()">
                <input type="text" placeholder="ex: nome" value="${key}"
                    class="qual-field-key input-dark col-span-2 px-3 py-2 rounded-lg text-xs font-mono" onchange="serializeQualFields()">
                <select class="qual-ghl-field-select input-dark col-span-3 px-3 py-2 rounded-lg text-xs font-mono" onchange="serializeQualFields()" ${ghlFieldId ? `data-ghl-field="${ghlFieldId}"` : ''}>
                    <option value="">-- Nao mapear --</option>
                    ${_ghlFieldOptions(ghlFieldId)}
                </select>
                <label class="col-span-3 flex items-center justify-center gap-1.5 cursor-pointer group" title="IA analisa a conversa e preenche sozinha">
                    <input type="checkbox" class="qual-field-auto w-3.5 h-3.5 text-brand-red bg-gray-900 border-gray-700 rounded focus:ring-brand-red" ${auto ? 'checked' : ''} onchange="toggleAutoField(this); serializeQualFields()">
                    <span class="text-[10px] font-mono ${auto ? 'text-brand-red' : 'text-gray-600'} group-hover:text-gray-400 transition-colors">IA analisa</span>
                </label>
                <button type="button" onclick="removeQualField(this)"
                    class="col-span-1 p-2 text-gray-600 hover:text-red-400 transition-colors justify-self-center">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                </button>
            `;
            container.appendChild(row);
            _updateFieldsVisibility();
            serializeQualFields();
        }

        function toggleAutoField(checkbox) {
            const row = checkbox.closest('.grid');
            const labelInput = row.querySelector('.qual-field-label');
            const autoLabel = checkbox.parentElement.querySelector('span');
            if (checkbox.checked) {
                labelInput.classList.add('border-brand-red/30');
                labelInput.placeholder = 'ex: Classificacao do Lead';
                autoLabel.classList.replace('text-gray-600', 'text-brand-red');
            } else {
                labelInput.classList.remove('border-brand-red/30');
                labelInput.placeholder = 'ex: Nome Completo';
                autoLabel.classList.replace('text-brand-red', 'text-gray-600');
            }
        }

        function autoGenerateKey(input) {
            const row = input.closest('.grid');
            const keyInput = row.querySelector('.qual-field-key');
            if (keyInput && !keyInput.value) {
                keyInput.value = input.value.toLowerCase()
                    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
                    .replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
            }
        }

        function removeQualField(btn) {
            btn.closest('.grid').remove();
            _updateFieldsVisibility();
            serializeQualFields();
        }

        function _updateFieldsVisibility() {
            const container = document.getElementById('qual_fields_container');
            const hasFields = container.children.length > 0;
            document.getElementById('qual_fields_header').classList.toggle('hidden', !hasFields);
            document.getElementById('qual_fields_empty').classList.toggle('hidden', hasFields);
        }

        function serializeQualFields() {
            const rows = document.querySelectorAll('#qual_fields_container > div');
            const fields = [];
            rows.forEach(row => {
                const label = row.querySelector('.qual-field-label')?.value?.trim() || '';
                const key = row.querySelector('.qual-field-key')?.value?.trim() || '';
                const ghlFieldId = row.querySelector('.qual-ghl-field-select')?.value || '';
                const auto = row.querySelector('.qual-field-auto')?.checked || false;
                if (label && key) {
                    fields.push({ label, key, ghl_field_id: ghlFieldId, auto });
                }
            });
            document.getElementById('ai_qual_fields_json').value = JSON.stringify(fields);
        }

        // ===== HISTORICO TAB =====
        let _histOffset = 0;
        let _histTotal = 0;
        let _histLoading = false;
        let _qualFields = [];

        async function loadConversations(reset = true) {
            const locationId = document.getElementById('ai_location_id').value;
            if (!locationId) return;

            // Ensure _qualFields is set from row data if not yet loaded
            if (_qualFields.length === 0) {
                const row = document.querySelector(`tr[data-location="${locationId}"]`);
                if (row) {
                    try { _qualFields = JSON.parse(row.dataset.aiqualfields || '[]'); } catch(e) {}
                }
            }

            const container = document.getElementById('hist_contacts_list');
            const loadingEl = document.getElementById('hist_loading');
            const emptyEl = document.getElementById('hist_empty');
            const counterEl = document.getElementById('hist_counter');

            if (reset) {
                _histOffset = 0;
                container.innerHTML = '';
                _initHistScroll();
            }
            if (_histLoading) return;
            _histLoading = true;
            loadingEl.classList.remove('hidden');
            emptyEl.classList.add('hidden');

            _startHistAutoRefresh();

            try {
                const resp = await fetch(`/admin/agents/${locationId}/conversations?offset=${_histOffset}&limit=20`);
                const data = await resp.json();
                if (data.success) {
                    _histTotal = data.total;
                    counterEl.textContent = `(${_histTotal})`;
                    if (data.qualification_fields && data.qualification_fields.length > 0) _qualFields = data.qualification_fields;

                    if (data.contacts.length === 0 && _histOffset === 0) {
                        emptyEl.classList.remove('hidden');
                    }

                    data.contacts.forEach(c => {
                        container.insertAdjacentHTML('beforeend', _renderContactCard(c));
                    });
                    _histOffset += data.contacts.length;
                }
            } catch(e) {
                console.error('Erro ao carregar historico:', e);
            } finally {
                _histLoading = false;
                loadingEl.classList.add('hidden');
            }
        }

        function _loadQualFieldsIfEmpty() {
            if (_qualFields.length > 0) return;
            const locationId = document.getElementById('ai_location_id')?.value;
            if (!locationId) return;
            const row = document.querySelector('tr[data-location="' + locationId + '"]');
            if (row) {
                try { _qualFields = JSON.parse(row.dataset.aiqualfields || '[]'); } catch(e) {}
            }
        }

        function _renderQualTooltip(progressData, isQualified, phone) {
            _loadQualFieldsIfEmpty();
            if (!_qualFields.length) return '<p class="text-[10px] text-gray-500 font-mono italic">Sem campos de qualificacao configurados</p>';
            const data = progressData || {};
            const collectFields = _qualFields.filter(f => !f.auto);
            const autoFields = _qualFields.filter(f => f.auto);
            const renderField = (f, isAuto) => {
                const val = data[f.key];
                const collected = val === true || (typeof val === 'string' && val !== '');
                const confirmedValue = isQualified && typeof val === 'string' && val !== '' ? val : null;
                const icon = isAuto ? (collected ? '&#129302;' : '&#9675;') : (collected ? '&#10003;' : '&#9675;');
                return `<div class="flex items-start gap-2 py-0.5">
                    <span class="${collected ? (isAuto ? 'text-brand-red' : 'text-green-400') : 'text-gray-600'} text-[10px] mt-0.5 shrink-0">${icon}</span>
                    <div class="min-w-0">
                        <span class="text-[10px] font-mono ${collected ? 'text-gray-300' : 'text-gray-500'}">${_escapeHtml(f.label)}${isAuto ? ' <span class="text-[8px] text-brand-red/70">AUTO</span>' : ''}</span>
                        ${confirmedValue ? `<p class="text-[9px] font-mono text-gray-500 truncate max-w-[160px]">${_escapeHtml(String(confirmedValue))}</p>` : ''}
                    </div>
                </div>`;
            };
            const items = [
                ...collectFields.map(f => renderField(f, false)),
                ...autoFields.map(f => renderField(f, true)),
            ];
            const countCollected = collectFields.filter(f => {
                const v = data[f.key];
                return v === true || (typeof v === 'string' && v !== '');
            }).length;
            const headerColor = isQualified ? 'text-green-400' : 'text-gray-400';
            const label = isQualified ? 'Qualificado ✓' : `Campos coletados (${countCollected}/${collectFields.length})`;
            const resetBtn = isQualified && phone
                ? `<button type="button" onclick="event.stopPropagation();resetQualification('${phone}')"
                    class="mt-2 w-full text-[9px] text-red-400 hover:text-red-300 font-mono border border-red-800/50 hover:border-red-600 rounded px-2 py-1 transition-colors">
                    Resetar Qualificacao
                  </button>`
                : '';
            return `<div class="qual-tooltip-content">
                <p class="text-[9px] font-bold ${headerColor} uppercase tracking-widest mb-2">${label}</p>
                <div class="space-y-1">${items.join('')}</div>
                ${resetBtn}
            </div>`;
        }

        function _renderContactCard(c) {
            const phone = c.phone;
            const displayPhone = phone.replace(/^(\d{2})(\d{2})(\d{4,5})(\d{4})$/, '+$1 ($2) $3-$4');
            const lastMsg = c.last_msg ? new Date(c.last_msg).toLocaleString('pt-BR') : '-';
            const statusBadge = c.qualified
                ? '<span class="hist-badge px-2 py-0.5 bg-green-500/20 text-green-400 text-[9px] font-bold uppercase rounded-full">Qualificado</span>'
                : '<span class="hist-badge px-2 py-0.5 bg-blue-500/20 text-blue-400 text-[9px] font-bold uppercase rounded-full">Em atendimento</span>';

            const qualData = c.qualified ? c.qualified.qualified_data : null;
            const qualBtnColor = c.qualified ? 'text-green-400' : 'text-gray-500';
            const qualDataJson = JSON.stringify(qualData || {}).replace(/\\/g, '\\\\').replace(/'/g, "\\'");

            return `
                <div class="hist-card border border-gray-800 rounded-lg overflow-hidden hover:border-gray-700 transition-colors" data-phone="${phone}">
                    <div class="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-white/5 transition-colors">
                        <button type="button" onclick="toggleHistCard(this, '${c.session_id}', '${phone}')"
                            class="flex items-center gap-3 min-w-0 flex-1">
                            <div class="w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center text-xs font-bold text-gray-400 shrink-0">
                                ${phone.slice(-2)}
                            </div>
                            <div class="min-w-0">
                                <p class="text-sm font-mono text-white truncate">${displayPhone || phone}</p>
                                <p class="text-[10px] text-gray-500 font-mono">${c.msg_count} msgs · ${lastMsg}</p>
                            </div>
                        </button>
                        <div class="flex items-center gap-2 shrink-0">
                            <div class="relative qual-tooltip-wrap">
                                <button type="button" onclick="toggleQualTooltip(event, this)" onmouseenter="showQualTooltip(this)" onmouseleave="hideQualTooltip(this)"
                                    class="${qualBtnColor} hover:text-white transition-colors p-1" title="Campos coletados">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/>
                                    </svg>
                                </button>
                                <div class="qual-tooltip hidden absolute right-0 top-full mt-1 z-50 bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-3 min-w-[220px]"
                                    data-qual='${qualDataJson}'>
                                </div>
                            </div>
                            ${statusBadge}
                            <button type="button" onclick="toggleHistCard(this, '${c.session_id}', '${phone}')" class="p-0.5">
                                <svg class="hist-chevron w-4 h-4 text-gray-600 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                                </svg>
                            </button>
                        </div>
                    </div>
                    <div class="hist-detail hidden">
                        ${c.qualified ? `<div class="ai-stopped-notice flex items-center gap-2 px-4 py-2 bg-yellow-500/10 border-b border-yellow-700/30">
                            <span class="text-yellow-400 text-[10px]">&#9888;</span>
                            <p class="text-[10px] text-yellow-400 font-mono">Agente IA desativado — lead qualificado. Use "Resetar Qualificacao" abaixo para reativar.</p>
                        </div>` : ''}
                        <div class="hist-messages px-4 py-3 border-t border-gray-800 max-h-80 overflow-y-auto space-y-2">
                            <p class="text-center text-gray-600 text-xs font-mono py-4">Carregando mensagens...</p>
                        </div>
                        <div class="flex items-center justify-end px-4 py-2 border-t border-gray-800/60">
                            <button type="button" onclick="resetQualification('${phone}')"
                                class="flex items-center gap-1.5 text-[10px] text-gray-500 hover:text-red-400 font-mono transition-colors"
                                title="Apaga o historico e reativa o agente para este contato">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                                </svg>
                                Resetar conversa
                            </button>
                        </div>
                        ${c.qualified ? `
                        <div class="px-4 py-3 border-t border-gray-800 bg-green-500/5">
                            <div class="flex items-center justify-between mb-1">
                                <p class="text-[10px] font-bold text-green-400 uppercase tracking-widest">Dados da Qualificacao</p>
                                <button type="button" onclick="resetQualification('${phone}')"
                                    class="text-[9px] text-red-400 hover:text-red-300 font-mono border border-red-800/50 hover:border-red-600 rounded px-2 py-0.5 transition-colors"
                                    title="Remove a qualificacao e reativa o agente para este contato">
                                    Resetar Qualificacao
                                </button>
                            </div>
                            <pre class="text-[10px] text-gray-400 font-mono whitespace-pre-wrap">${JSON.stringify(c.qualified.qualified_data || {}, null, 2)}</pre>
                            ${c.qualified.summary ? `<p class="text-[10px] text-gray-500 font-mono mt-2 italic">${c.qualified.summary.substring(0, 300)}</p>` : ''}
                        </div>` : ''}
                    </div>
                </div>`;
        }

        let _openCardPhone = null;
        let _autoRefreshInterval = null;

        function _renderMessageBubbles(messages) {
            return messages.map(m => {
                const time = m.created_at ? new Date(m.created_at).toLocaleTimeString('pt-BR', {hour:'2-digit', minute:'2-digit'}) : '';
                if (m.type === 'human') {
                    return `<div class="flex justify-end"><div class="bg-green-900/40 border border-green-800/30 rounded-lg px-3 py-1.5 max-w-[80%]">
                        <p class="text-xs text-gray-200 font-mono">${_escapeHtml(m.content)}</p>
                        <p class="text-[9px] text-gray-500 text-right mt-0.5">${time}</p>
                    </div></div>`;
                } else {
                    return `<div class="flex justify-start"><div class="bg-gray-800/60 border border-gray-700/30 rounded-lg px-3 py-1.5 max-w-[80%]">
                        <p class="text-xs text-gray-300 font-mono">${_escapeHtml(m.content)}</p>
                        <p class="text-[9px] text-gray-500 mt-0.5">${time}</p>
                    </div></div>`;
                }
            }).join('');
        }

        async function _fetchMessages(phone, msgContainer, silent = false) {
            const locationId = document.getElementById('ai_location_id').value;
            if (!locationId) return;
            const wasAtBottom = msgContainer.scrollHeight - msgContainer.scrollTop <= msgContainer.clientHeight + 20;
            if (!silent) msgContainer.innerHTML = '<p class="text-center text-gray-600 text-xs font-mono py-4">Carregando mensagens...</p>';
            try {
                const resp = await fetch(`/admin/agents/${locationId}/conversations/${encodeURIComponent(phone)}/messages?limit=100`);
                const data = await resp.json();
                if (data.success && data.messages.length > 0) {
                    msgContainer.innerHTML = _renderMessageBubbles(data.messages);
                    if (!silent || wasAtBottom) msgContainer.scrollTop = msgContainer.scrollHeight;
                } else if (!silent) {
                    msgContainer.innerHTML = '<p class="text-center text-gray-600 text-xs font-mono py-4">Nenhuma mensagem encontrada.</p>';
                }
            } catch(e) {
                if (!silent) msgContainer.innerHTML = '<p class="text-center text-red-400 text-xs font-mono py-4">Erro ao carregar mensagens.</p>';
            }
        }

        async function toggleHistCard(btn, sessionId, phone) {
            const card = btn.closest('.hist-card');
            const detail = card.querySelector('.hist-detail');
            const chevron = card.querySelector('.hist-chevron');
            const isOpen = !detail.classList.contains('hidden');

            if (isOpen) {
                detail.classList.add('hidden');
                chevron.style.transform = '';
                _openCardPhone = null;
                return;
            }

            // Close any other open card
            document.querySelectorAll('.hist-card .hist-detail:not(.hidden)').forEach(d => {
                d.classList.add('hidden');
                const ch = d.closest('.hist-card').querySelector('.hist-chevron');
                if (ch) ch.style.transform = '';
            });

            detail.classList.remove('hidden');
            chevron.style.transform = 'rotate(180deg)';
            _openCardPhone = phone;

            const msgContainer = card.querySelector('.hist-messages');
            await _fetchMessages(phone, msgContainer);
        }

        function _startHistAutoRefresh() {
            if (_autoRefreshInterval) return;
            _autoRefreshInterval = setInterval(async () => {
                // Refresh open card messages silently
                if (_openCardPhone) {
                    const openDetail = document.querySelector('.hist-card .hist-detail:not(.hidden)');
                    if (openDetail) {
                        const msgContainer = openDetail.querySelector('.hist-messages');
                        if (msgContainer) await _fetchMessages(_openCardPhone, msgContainer, true);
                    }
                }
                // Refresh contact list counts silently
                const locationId = document.getElementById('ai_location_id')?.value;
                if (!locationId) return;
                try {
                    const resp = await fetch(`/admin/agents/${locationId}/conversations?offset=0&limit=${Math.max(_histOffset, 20)}`);
                    const data = await resp.json();
                    if (data.success) {
                        _histTotal = data.total;
                        document.getElementById('hist_counter').textContent = `(${_histTotal})`;
                        // Update card headers without re-rendering (update count, date, and qualification badge)
                        data.contacts.forEach(c => {
                            const card = document.querySelector(`.hist-card[data-phone="${c.phone}"]`);
                            if (!card) return;
                            // Update message count + last date
                            const headerBtn = card.querySelector('button[onclick*="toggleHistCard"]');
                            if (headerBtn) {
                                const sub = headerBtn.querySelector('p:last-child');
                                if (sub) {
                                    const lastMsg = c.last_msg ? new Date(c.last_msg).toLocaleString('pt-BR') : '-';
                                    sub.textContent = `${c.msg_count} msgs · ${lastMsg}`;
                                }
                            }
                            // Update badge if qualification status changed
                            const badge = card.querySelector('.hist-badge');
                            if (badge) {
                                const isQualNow = !!c.qualified;
                                const wasQual = badge.classList.contains('text-green-400');
                                if (isQualNow !== wasQual) {
                                    badge.className = isQualNow
                                        ? 'hist-badge px-2 py-0.5 bg-green-500/20 text-green-400 text-[9px] font-bold uppercase rounded-full'
                                        : 'hist-badge px-2 py-0.5 bg-blue-500/20 text-blue-400 text-[9px] font-bold uppercase rounded-full';
                                    badge.textContent = isQualNow ? 'Qualificado' : 'Em atendimento';
                                    // Update qual button color
                                    const qualBtn = card.querySelector('.qual-tooltip-wrap button');
                                    if (qualBtn) qualBtn.className = (isQualNow ? 'text-green-400' : 'text-gray-500') + ' hover:text-white transition-colors p-1';
                                    // Add/remove AI-stopped notice in open detail
                                    const detail = card.querySelector('.hist-detail');
                                    if (detail && !detail.classList.contains('hidden')) {
                                        const existing = detail.querySelector('.ai-stopped-notice');
                                        if (isQualNow && !existing) {
                                            detail.insertAdjacentHTML('afterbegin', `
                                                <div class="ai-stopped-notice flex items-center gap-2 px-4 py-2 bg-yellow-500/10 border-b border-yellow-700/30">
                                                    <span class="text-yellow-400 text-[10px]">&#9888;</span>
                                                    <p class="text-[10px] text-yellow-400 font-mono">Agente IA desativado — lead qualificado. Use "Resetar Qualificacao" para reativar.</p>
                                                </div>`);
                                        } else if (!isQualNow && existing) {
                                            existing.remove();
                                        }
                                    }
                                }
                            }
                        });
                    }
                } catch(e) {}
            }, 30000);
        }

        function _stopHistAutoRefresh() {
            if (_autoRefreshInterval) {
                clearInterval(_autoRefreshInterval);
                _autoRefreshInterval = null;
            }
        }

        function _escapeHtml(text) {
            const d = document.createElement('div');
            d.textContent = text;
            return d.innerHTML;
        }

        async function resetQualification(phone) {
            const locationId = document.getElementById('ai_location_id')?.value;
            if (!locationId) return;
            if (!confirm(
                'Resetar qualificação deste contato?\n\n' +
                'Isso vai remover a qualificação E o histórico de conversa, ' +
                'para que o agente possa responder novamente do zero.'
            )) return;
            try {
                const resp = await fetch(
                    `/admin/agents/${locationId}/conversations/${encodeURIComponent(phone)}/qualification?clear_history=true`,
                    { method: 'DELETE' }
                );
                const data = await resp.json();
                if (data.success) {
                    _openCardPhone = null;
                    await loadConversations(true);
                } else {
                    alert('Erro ao resetar: ' + (data.error || 'desconhecido'));
                }
            } catch(e) {
                alert('Erro de rede ao resetar qualificação.');
            }
        }

        async function showQualTooltip(btn) {
            _loadQualFieldsIfEmpty();
            const wrap = btn.closest('.qual-tooltip-wrap');
            const tooltip = wrap.querySelector('.qual-tooltip');
            const phone = wrap.closest('.hist-card')?.dataset.phone;
            let qualData = {};
            try { qualData = JSON.parse(tooltip.dataset.qual || '{}'); } catch(e) {}

            // Show immediately with stored data, then update with progress
            const hasQualData = Object.keys(qualData).length > 0;
            tooltip.innerHTML = _renderQualTooltip(qualData, hasQualData, phone);
            tooltip.classList.remove('hidden');
            if (!hasQualData && _qualFields.length > 0) {
                const locationId = document.getElementById('ai_location_id')?.value;
                if (locationId && phone) {
                    try {
                        const resp = await fetch(`/admin/agents/${locationId}/conversations/${encodeURIComponent(phone)}/progress`);
                        const data = await resp.json();
                        if (data.success && Object.keys(data.progress || {}).length > 0) {
                            tooltip.innerHTML = _renderQualTooltip(data.progress, data.qualified, phone);
                        }
                    } catch(e) {}
                }
            }
        }
        function hideQualTooltip(btn) {
            const wrap = btn.closest('.qual-tooltip-wrap');
            const tooltip = wrap.querySelector('.qual-tooltip');
            tooltip.classList.add('hidden');
        }
        function toggleQualTooltip(e, btn) {
            e.stopPropagation();
            // Close all other tooltips first
            document.querySelectorAll('.qual-tooltip').forEach(t => t.classList.add('hidden'));
            const wrap = btn.closest('.qual-tooltip-wrap');
            const tooltip = wrap.querySelector('.qual-tooltip');
            if (tooltip.classList.contains('hidden')) {
                showQualTooltip(btn);
            } else {
                tooltip.classList.add('hidden');
            }
        }
        // Close tooltips on outside click
        document.addEventListener('click', () => {
            document.querySelectorAll('.qual-tooltip').forEach(t => t.classList.add('hidden'));
        });

        function _initHistScroll() {
            const scrollContainer = document.getElementById('hist_scroll_area');
            if (!scrollContainer) return;
            scrollContainer.addEventListener('scroll', () => {
                if (scrollContainer.scrollTop + scrollContainer.clientHeight >= scrollContainer.scrollHeight - 100) {
                    if (!_histLoading && _histOffset < _histTotal) {
                        loadConversations(false);
                    }
                }
            });
        }

        // ─── Prompt History Modal ─────────────────────────────────────
        let _historyContext = { locationId: null, channel: 'whatsapp', selectedId: null };

        async function openPromptHistoryModal() {
            const locationId = document.getElementById('ai_location_id').value;
            const channel = document.getElementById('ai_channel').value || 'whatsapp';
            if (!locationId) { alert('Salve o agente primeiro.'); return; }
            _historyContext = { locationId, channel, selectedId: null };

            document.getElementById('promptHistoryModal').classList.remove('hidden');
            document.getElementById('prompt_history_loading').classList.remove('hidden');
            document.getElementById('prompt_history_empty').classList.add('hidden');
            document.getElementById('prompt_history_list').innerHTML = '';
            document.getElementById('prompt_history_detail').classList.add('hidden');

            try {
                const resp = await fetch(`/admin/agents/${locationId}/prompt-history?channel=${encodeURIComponent(channel)}&limit=30`);
                const data = await resp.json();
                document.getElementById('prompt_history_loading').classList.add('hidden');
                if (!data.success || !data.history || data.history.length === 0) {
                    document.getElementById('prompt_history_empty').classList.remove('hidden');
                    return;
                }
                _renderPromptHistoryList(data.history);
            } catch (e) {
                document.getElementById('prompt_history_loading').classList.add('hidden');
                alert('Erro ao carregar histórico.');
            }
        }

        function _renderPromptHistoryList(items) {
            const sourceLabels = {
                form: { label: 'Formulário', color: 'text-purple-400' },
                regenerate: { label: 'Regenerado', color: 'text-blue-400' },
                optimize_apply: { label: 'Melhoria aplicada', color: 'text-amber-400' },
                manual_save: { label: 'Salvo manual', color: 'text-gray-400' },
                restore: { label: 'Restaurado', color: 'text-green-400' },
            };
            const html = items.map(it => {
                const s = sourceLabels[it.source] || { label: it.source, color: 'text-gray-400' };
                const noteHtml = it.note ? `<p class="text-[10px] text-gray-500 italic mt-1">"${it.note}"</p>` : '';
                return `<div class="bg-black/30 border border-gray-800 rounded-lg p-3 hover:border-gray-700 cursor-pointer transition-colors" onclick="viewPromptVersion(${it.id})">
                    <div class="flex items-center justify-between gap-2 mb-1">
                        <span class="${s.color} text-[10px] font-bold uppercase tracking-widest font-mono">${s.label}</span>
                        <span class="text-[10px] text-gray-600 font-mono">${(it.created_at || '').replace('T',' ').slice(0,16)}</span>
                    </div>
                    <p class="text-xs text-gray-300 font-mono line-clamp-2">${(it.prompt_preview || '').replace(/</g,'&lt;')}</p>
                    <p class="text-[10px] text-gray-600 font-mono mt-1">${it.prompt_length} caracteres</p>
                    ${noteHtml}
                </div>`;
            }).join('');
            document.getElementById('prompt_history_list').innerHTML = html;
        }

        async function viewPromptVersion(historyId) {
            try {
                const resp = await fetch(`/admin/agents/prompt-history/${historyId}`);
                const data = await resp.json();
                if (!data.success || !data.version) {
                    alert('Versão não encontrada.');
                    return;
                }
                _historyContext.selectedId = historyId;
                document.getElementById('prompt_history_list').classList.add('hidden');
                document.getElementById('prompt_history_detail').classList.remove('hidden');
                document.getElementById('prompt_history_detail_meta').textContent =
                    `#${data.version.id} · ${data.version.source} · ${(data.version.created_at||'').replace('T',' ').slice(0,16)}`;
                document.getElementById('prompt_history_detail_content').textContent = data.version.prompt;
            } catch (e) {
                alert('Erro ao carregar versão.');
            }
        }

        function closePromptHistoryDetail() {
            _historyContext.selectedId = null;
            document.getElementById('prompt_history_detail').classList.add('hidden');
            document.getElementById('prompt_history_list').classList.remove('hidden');
        }

        async function restorePromptVersion() {
            if (!_historyContext.selectedId) return;
            if (!confirm('Restaurar esta versão? O prompt atual será sobrescrito (mas continua salvo no histórico).')) return;
            try {
                const resp = await fetch(`/admin/agents/prompt-history/${_historyContext.selectedId}/restore`, { method: 'POST' });
                const data = await resp.json();
                if (data.success) {
                    alert('Versão restaurada. Recarregue o agente para ver o novo prompt.');
                    closePromptHistoryModal();
                } else {
                    alert('Erro: ' + (data.error || 'desconhecido'));
                }
            } catch (e) {
                alert('Erro de rede ao restaurar.');
            }
        }

        function closePromptHistoryModal() {
            document.getElementById('promptHistoryModal').classList.add('hidden');
        }

        function closeAIAgentModal() {
            document.getElementById('aiAgentModal').classList.add('hidden');
            _stopHistAutoRefresh();
            _openCardPhone = null;
        }

        async function loadUsageData() {
            const locationId = document.getElementById('ai_location_id').value;
            const period = document.getElementById('costs_period').value;
            const loadingEl = document.getElementById('costs_loading');
            const emptyEl = document.getElementById('costs_empty');
            const dataEl = document.getElementById('costs_data');

            loadingEl.classList.remove('hidden');
            emptyEl.classList.add('hidden');
            dataEl.classList.add('hidden');

            try {
                const resp = await fetch(`/admin/api/usage/${locationId}?period=${period}`);
                const data = await resp.json();
                loadingEl.classList.add('hidden');

                if (data.total_calls === 0) {
                    emptyEl.classList.remove('hidden');
                    return;
                }

                dataEl.classList.remove('hidden');

                // Summary cards
                document.getElementById('costs_total_calls').textContent = data.total_calls.toLocaleString();
                document.getElementById('costs_total_cost').textContent = '$' + data.total_cost_usd.toFixed(4);

                let totalTokens = 0;
                let totalChars = 0;
                for (const [svc, info] of Object.entries(data.by_service)) {
                    totalTokens += (info.input_tokens || 0) + (info.output_tokens || 0);
                    totalChars += info.characters || 0;
                }
                document.getElementById('costs_total_tokens').textContent = totalTokens.toLocaleString();
                document.getElementById('costs_total_chars').textContent = totalChars.toLocaleString();

                // Service breakdown table
                const serviceColors = { openrouter: 'text-blue-400', elevenlabs: 'text-purple-400', groq: 'text-green-400' };
                const serviceIcons = { openrouter: '🤖', elevenlabs: '🗣️', groq: '🎙️' };
                const tbody = document.getElementById('costs_service_rows');
                tbody.innerHTML = '';
                for (const [svc, info] of Object.entries(data.by_service)) {
                    const color = serviceColors[svc] || 'text-gray-300';
                    const icon = serviceIcons[svc] || '⚡';
                    tbody.innerHTML += `<tr class="hover:bg-gray-800/30 transition-colors">
                        <td class="px-4 py-3 font-mono font-bold ${color}">${icon} ${svc.charAt(0).toUpperCase() + svc.slice(1)}</td>
                        <td class="px-4 py-3 text-right text-gray-300 font-mono">${info.calls.toLocaleString()}</td>
                        <td class="px-4 py-3 text-right text-gray-400 font-mono">${(info.input_tokens || 0).toLocaleString()}</td>
                        <td class="px-4 py-3 text-right text-gray-400 font-mono">${(info.output_tokens || 0).toLocaleString()}</td>
                        <td class="px-4 py-3 text-right text-gray-400 font-mono">${(info.characters || 0).toLocaleString()}</td>
                        <td class="px-4 py-3 text-right text-brand-red font-mono font-bold">$${info.cost_usd.toFixed(4)}</td>
                    </tr>`;
                }

                // Daily bar chart
                const dailyEntries = Object.entries(data.daily);
                const barsEl = document.getElementById('costs_daily_bars');
                const labelsEl = document.getElementById('costs_daily_labels');
                barsEl.innerHTML = '';
                labelsEl.innerHTML = '';

                if (dailyEntries.length > 0) {
                    const maxCalls = Math.max(...dailyEntries.map(([, d]) => d.calls));
                    for (const [day, info] of dailyEntries) {
                        const heightPct = maxCalls > 0 ? Math.max((info.calls / maxCalls) * 100, 4) : 4;
                        const shortDay = day.slice(5); // MM-DD
                        barsEl.innerHTML += `<div class="flex-1 bg-brand-red/60 hover:bg-brand-red rounded-t transition-colors cursor-default relative group" style="height: ${heightPct}%" title="${day}: ${info.calls} calls">
                            <div class="absolute -top-6 left-1/2 -translate-x-1/2 bg-gray-900 text-[9px] text-white font-mono px-1.5 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap border border-gray-700">${info.calls}</div>
                        </div>`;
                        labelsEl.innerHTML += `<div class="flex-1 text-center text-[8px] text-gray-600 font-mono truncate">${shortDay}</div>`;
                    }
                }

            } catch (err) {
                loadingEl.classList.add('hidden');
                emptyEl.classList.remove('hidden');
                console.error('Error loading usage data:', err);
            }
        }

        async function openQRModal(locationId, companyName) {
            document.getElementById('qr_company_name').innerText = companyName;
            const qrContainer = document.getElementById('qr_image_container');
            qrContainer.innerHTML = '<div class="animate-pulse w-48 h-48 bg-gray-800 rounded-xl flex items-center justify-center border border-gray-700 mx-auto"><span class="text-xs text-brand-red font-mono uppercase tracking-widest animate-bounce">Loading...</span></div>';
            document.getElementById('qrModal').classList.remove('hidden');

            try {
                const response = await fetch(`/admin/tenant/${locationId}/qrcode`);
                const data = await response.json();

                if (data.qrcode) {
                    qrContainer.innerHTML = `<img src="${data.qrcode}" alt="Scan to connect" class="mx-auto rounded-xl w-64 h-64 border-2 border-brand-red/50 shadow-[0_0_30px_rgba(225,29,72,0.2)]">`;
                } else {
                    qrContainer.innerHTML = `<div class="w-48 h-48 bg-gray-900 rounded-xl flex flex-col gap-2 items-center justify-center border border-brand-red/50 mx-auto p-4 text-center"><svg class="w-8 h-8 text-brand-red" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg><span class="text-xs text-gray-400 font-mono focus:outline-none focus:ring-1">${data.error || 'Failed to load code'}</span></div>`;
                }
            } catch (err) {
                qrContainer.innerHTML = '<div class="text-xs text-red-500 font-mono">Connection error</div>';
            }
        }

        function closeQRModal() {
            document.getElementById('qrModal').classList.add('hidden');
            // Refresh to update the UI connectivity status
            setTimeout(() => window.location.reload(), 500);
        }

        // ── Conexão WhatsApp via WAHA (gestão de sessão pelo painel) ──
        let _wahaLoc = null;
        let _wahaPoll = null;

        function _wahaSpinner(msg) {
            return `<div class="animate-pulse w-full py-10 flex items-center justify-center"><span class="text-xs text-emerald-400 font-mono uppercase tracking-widest">${msg}</span></div>`;
        }
        function _wahaError(msg) {
            return `<div class="text-xs text-red-400 font-mono py-8">${msg}</div>`;
        }
        const _WAHA_BADGE = {
            WORKING: 'text-emerald-300 bg-emerald-500/15',
            SCAN_QR_CODE: 'text-amber-300 bg-amber-500/15',
            STARTING: 'text-sky-300 bg-sky-500/15',
            STOPPED: 'text-gray-400 bg-gray-600/20',
            FAILED: 'text-red-300 bg-red-500/15',
        };
        function _setWahaBadge(status) {
            const b = document.getElementById('waha_status_badge');
            b.className = 'ml-2 px-2 py-0.5 rounded text-[10px] normal-case tracking-normal ' + (_WAHA_BADGE[status] || 'status-badge');
            b.innerText = status || '—';
        }

        function openWahaModal(locationId, companyName) {
            _wahaLoc = locationId;
            document.getElementById('waha_company_name').innerText = companyName || locationId;
            _setWahaBadge('');
            document.getElementById('waha_content').innerHTML = _wahaSpinner('Carregando status…');
            document.getElementById('wahaModal').classList.remove('hidden');
            refreshWahaStatus();
        }
        function closeWahaModal() {
            stopWahaPolling();
            document.getElementById('wahaModal').classList.add('hidden');
        }
        function stopWahaPolling() {
            if (_wahaPoll) { clearInterval(_wahaPoll); _wahaPoll = null; }
        }
        function startWahaPolling() {
            stopWahaPolling();
            _wahaPoll = setInterval(async () => {
                try {
                    const r = await fetch(`/admin/waha/tenant/${_wahaLoc}/status`);
                    const d = await r.json();
                    if (d.status === 'SCAN_QR_CODE') { _setWahaBadge('SCAN_QR_CODE'); showWahaQr(); }
                    else { renderWahaContent(d); }
                } catch (e) { /* silencioso, tenta de novo */ }
            }, 3000);
        }

        async function refreshWahaStatus() {
            if (!_wahaLoc) return;
            try {
                const r = await fetch(`/admin/waha/tenant/${_wahaLoc}/status`);
                renderWahaContent(await r.json());
            } catch (e) {
                document.getElementById('waha_content').innerHTML = _wahaError('Erro ao consultar status.');
            }
        }

        function renderWahaContent(d) {
            const c = document.getElementById('waha_content');
            if (d.error) { c.innerHTML = _wahaError(d.error); return; }
            if (d.configured === false) {
                _setWahaBadge('');
                c.innerHTML = `<div class="py-6 text-center">
                    <p class="text-xs text-gray-400 font-mono mb-4">Servidor WAHA não configurado.</p>
                    <button onclick="closeWahaModal(); openSystemModal();" title="Abrir Configurações Globais" class="btn-brand px-5 py-2 rounded-lg text-white font-bold text-xs uppercase tracking-widest font-mono">Configurar WAHA</button>
                </div>`;
                return;
            }
            _setWahaBadge(d.status);
            if (d.status === 'WORKING') {
                stopWahaPolling();
                const me = d.me ? (typeof d.me === 'string' ? d.me : JSON.stringify(d.me)) : '';
                c.innerHTML = `<div class="py-4 text-center w-full">
                    <div class="w-14 h-14 rounded-full bg-emerald-500/15 border border-emerald-500/40 flex items-center justify-center mx-auto mb-3">
                        <svg class="w-7 h-7 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                    </div>
                    <p class="text-sm font-bold text-white mb-1">Conectado</p>
                    <p class="text-[11px] text-gray-500 font-mono mb-6 break-all">${me}</p>
                    <div class="flex gap-2">
                        <button onclick="wahaSessionAction('restart')" title="Reiniciar a sessão do WhatsApp" class="flex-1 px-3 py-2 rounded-lg text-[10px] font-bold uppercase tracking-widest text-sky-300 bg-sky-500/10 border border-sky-500/30 hover:bg-sky-500/20 transition-colors font-mono">Reiniciar</button>
                        <button onclick="wahaSessionAction('disconnect')" title="Desconectar e remover a sessão deste número" class="flex-1 px-3 py-2 rounded-lg text-[10px] font-bold uppercase tracking-widest text-red-300 bg-red-500/10 border border-red-500/30 hover:bg-red-500/20 transition-colors font-mono">Desconectar</button>
                    </div>
                </div>`;
                return;
            }
            if (d.status === 'STARTING') {
                c.innerHTML = _wahaSpinner('Iniciando sessão…');
                startWahaPolling();
                return;
            }
            if (d.status === 'SCAN_QR_CODE') {
                c.innerHTML = `<div class="w-full">
                    <div id="waha_qr_box" class="mb-4 flex items-center justify-center">${_wahaSpinner('Gerando QR…')}</div>
                    <p class="text-[10px] text-gray-500 font-mono leading-relaxed px-2 mb-4">No WhatsApp do número → <span class="text-white">Aparelhos conectados</span> → <span class="text-white">Conectar um aparelho</span> → escaneie.</p>
                    <button onclick="wahaSessionAction('disconnect')" title="Cancelar a conexão" class="px-4 py-2 rounded-lg text-[10px] font-bold uppercase tracking-widest text-gray-400 bg-gray-800 hover:bg-gray-700 transition-colors font-mono">Cancelar</button>
                </div>`;
                showWahaQr();
                startWahaPolling();
                return;
            }
            // STOPPED / UNKNOWN / FAILED / sessão ainda não criada
            const failed = d.status === 'FAILED';
            c.innerHTML = `<div class="py-6 text-center">
                <p class="text-xs ${failed ? 'text-red-400' : 'text-gray-400'} font-mono mb-5">${failed ? 'Sessão falhou. Reconecte.' : 'Nenhum número conectado a esta instância.'}</p>
                <button onclick="connectWaha(this)" title="Criar a sessão e gerar o QR code" class="btn-brand px-6 py-2.5 rounded-lg text-white font-bold text-sm uppercase tracking-widest font-mono">${failed ? 'Reconectar' : 'Conectar número'}</button>
            </div>`;
        }

        async function connectWaha(btn) {
            if (btn) { btn.disabled = true; btn.innerText = 'Conectando…'; }
            document.getElementById('waha_content').innerHTML = _wahaSpinner('Criando sessão…');
            try {
                const r = await fetch(`/admin/waha/tenant/${_wahaLoc}/connect`, { method: 'POST' });
                const d = await r.json();
                if (d.error) { document.getElementById('waha_content').innerHTML = _wahaError(d.error); return; }
                setTimeout(refreshWahaStatus, 900);
            } catch (e) {
                document.getElementById('waha_content').innerHTML = _wahaError('Falha ao conectar.');
            }
        }

        function showWahaQr() {
            const box = document.getElementById('waha_qr_box');
            if (!box) return;
            const img = new Image();
            img.className = 'mx-auto rounded-xl w-56 h-56 border-2 border-emerald-500/40 bg-white p-1';
            img.alt = 'QR code para conectar o WhatsApp';
            img.onload = () => { box.innerHTML = ''; box.appendChild(img); };
            img.onerror = () => { if (!box.querySelector('img')) box.innerHTML = _wahaSpinner('Aguardando QR…'); };
            img.src = `/admin/waha/tenant/${_wahaLoc}/qr?t=${Date.now()}`;
        }

        async function wahaSessionAction(action) {
            if (action === 'disconnect' && !confirm('Desconectar este número? A sessão será removida do WAHA.')) return;
            stopWahaPolling();
            document.getElementById('waha_content').innerHTML = _wahaSpinner('Processando…');
            try {
                await fetch(`/admin/waha/tenant/${_wahaLoc}/${action}`, { method: 'POST' });
            } catch (e) { /* ignore */ }
            setTimeout(refreshWahaStatus, 1200);
        }

        async function testWahaConnection(btn) {
            const out = document.getElementById('waha_test_result');
            out.innerText = 'testando…'; out.className = 'ml-2 text-[10px] font-mono text-gray-400';
            try {
                const r = await fetch('/admin/waha/settings/test', { method: 'POST' });
                const d = await r.json();
                if (d.ok) { out.innerText = `OK — ${d.sessions_count} sessão(ões)`; out.className = 'ml-2 text-[10px] font-mono text-emerald-400'; }
                else { out.innerText = d.error || 'falhou'; out.className = 'ml-2 text-[10px] font-mono text-red-400'; }
            } catch (e) { out.innerText = 'erro de rede'; out.className = 'ml-2 text-[10px] font-mono text-red-400'; }
        }

        function switchAITab(tabName, btn) {
            document.querySelectorAll('.ai-tab-content').forEach(el => el.classList.add('hidden'));
            const targetContent = document.getElementById('ai_tab_' + tabName);
            if (targetContent) targetContent.classList.remove('hidden');

            document.querySelectorAll('.ai-tab-btn').forEach(el => {
                el.classList.remove('text-brand-red', 'bg-brand-red/10', 'border-brand-red/30');
                el.classList.add('text-gray-500', 'border-transparent');
            });

            const activeBtn = btn || document.getElementById('btn_ai_tab_' + tabName);
            if (activeBtn) {
                activeBtn.classList.remove('text-gray-500', 'border-transparent');
                activeBtn.classList.add('text-brand-red', 'bg-brand-red/10', 'border-brand-red/30');
            }

            // Auto-refresh: start when history tab active, stop otherwise
            if (tabName === 'history') {
                _startHistAutoRefresh();
            } else {
                _stopHistAutoRefresh();
            }
        }

        function openSystemModal() {
            document.getElementById('systemModal').classList.remove('hidden');
        }

        function closeSystemModal() {
            document.getElementById('systemModal').classList.add('hidden');
        }

        window.lastImprovedPrompt = null;
        window.originalPromptForMaster = null;
        let masterChatHistory = [];
        let testHistory = [];

        function formatMasterText(text) {
            return text
                .replace(/</g, "&lt;").replace(/>/g, "&gt;")
                .replace(/\*\*(.*?)\*\*/g, '<strong class="text-white">$1</strong>')
                .replace(/\n\n/g, '<br><br>')
                .replace(/\n- /g, '<br>• ')
                .replace(/\n/g, '<br>');
        }

        // ctx = 'settings' | 'tester' — garante IDs únicos por contexto
        function showApplyBtn(ctx) {
            const btn = document.getElementById('btn_apply_master_' + ctx);
            if (btn) btn.classList.remove('hidden');
        }

        function applySuggestedPrompt(ctx) {
            const prompt = window.lastImprovedPrompt;
            if (!prompt) return;

            // Aplica o prompt no textarea (fica na aba Settings)
            const textarea = document.getElementById('ai_prompt');
            textarea.value = prompt;
            textarea.classList.add('ring-2', 'ring-brand-red', 'ring-offset-2', 'ring-offset-gray-900');
            setTimeout(() => textarea.classList.remove('ring-2', 'ring-brand-red', 'ring-offset-2', 'ring-offset-gray-900'), 1000);

            // Esconde o painel correto e mostra confirmação
            const panelId = ctx === 'tester' ? 'tester_analysis_result' : 'ai_analysis_result';
            const panel = document.getElementById(panelId);
            if (panel) {
                panel.innerHTML = `<div class="flex items-center gap-2 p-3 text-xs text-green-400 font-mono">
                    <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                    Prompt aplicado com sucesso! Vá para a aba Settings para revisar e salvar.
                </div>`;
                setTimeout(() => panel.classList.add('hidden'), 3000);
            }
        }

        async function analyzePrompt() {
            const promptText = document.getElementById('ai_prompt').value;
            const btn = document.getElementById('btn_analyze_prompt');
            const resultDiv = document.getElementById('ai_analysis_result');
            const resultContent = document.getElementById('ai_analysis_content');

            if (!promptText) return;

            // Reset master chat state
            masterChatHistory = [];
            window.originalPromptForMaster = promptText;

            btn.disabled = true;
            btn.innerHTML = '<span class="animate-pulse">✨ Analisando & Simulando...</span>';
            resultDiv.classList.remove('hidden');
            resultContent.innerHTML = '<span class="text-xs text-gray-400 font-mono animate-pulse">A IA Mestre está realizando um teste prático com o robô nos bastidores. Aguarde...</span>';

            try {
                const response = await fetch('/admin/agents/analyze-prompt', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt_text: promptText })
                });
                const data = await response.json();

                if (data.success) {
                    window.lastImprovedPrompt = data.improved_prompt;

                    let transcriptHtml = '';
                    if (data.simulation_transcript) {
                        transcriptHtml = `
                        <div class="mb-4 bg-brand-red/5 border border-brand-red/10 rounded-lg p-3">
                            <p class="text-[10px] text-brand-red font-bold uppercase tracking-widest mb-2 flex items-center gap-1">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"></path></svg>
                                Prova Real (Simulação Lead vs Agente):
                            </p>
                            <pre class="whitespace-pre-wrap text-[10px] text-gray-400 font-mono leading-relaxed bg-black/30 p-2 rounded">${data.simulation_transcript}</pre>
                        </div>`;
                    }

                    resultContent.innerHTML = transcriptHtml
                        + `<div class="text-xs text-gray-300 font-mono leading-relaxed">${formatMasterText(data.analysis)}</div>`
                        + masterChatHtml('settings');

                    if (data.improved_prompt) showApplyBtn('settings');
                } else {
                    resultContent.innerHTML = `<span class="text-brand-red font-bold">${data.error}</span>`;
                }
            } catch (err) {
                resultContent.innerHTML = `<span class="text-brand-red">Erro de conexão ao analisar o prompt.</span>`;
            } finally {
                btn.disabled = false;
                btn.innerHTML = '✨ Analisar Prompt';
            }
        }

        function masterChatHtml(ctx) {
            return `
            <div class="mt-5 pt-4 border-t border-gray-800">
                <p class="text-[10px] text-brand-red font-bold uppercase tracking-widest mb-3 flex items-center gap-1.5">
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"></path></svg>
                    Conversar com a IA Mestre
                </p>
                <p class="text-[10px] text-gray-500 font-mono mb-3">Dê seu feedback sobre o agente. A Mestre vai considerar e revisar o prompt se necessário.</p>
                <div id="master_chat_messages_${ctx}" class="space-y-2 mb-3 max-h-48 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-800 scrollbar-track-transparent"></div>
                <div class="flex gap-2">
                    <input type="text" id="master_chat_input_${ctx}"
                        class="input-dark flex-1 px-3 py-2 rounded-lg text-xs font-mono"
                        placeholder="Ex: o agente está sendo muito formal, precisa ser mais direto..."
                        onkeydown="if(event.key==='Enter'){event.preventDefault();sendMasterFeedback('${ctx}');}">
                    <button type="button" onclick="sendMasterFeedback('${ctx}')"
                        class="bg-brand-red/20 hover:bg-brand-red text-brand-red hover:text-white border border-brand-red/40 transition-colors px-3 py-2 rounded-lg text-xs font-bold font-mono uppercase tracking-widest">
                        Enviar
                    </button>
                    <button type="button" id="btn_apply_master_${ctx}" onclick="applySuggestedPrompt('${ctx}')"
                        class="hidden bg-green-600/20 hover:bg-green-600 text-green-400 hover:text-white border border-green-600/40 transition-colors px-3 py-2 rounded-lg text-xs font-bold font-mono uppercase tracking-widest whitespace-nowrap">
                        Aplicar Mudanças
                    </button>
                </div>
            </div>`;
        }

        async function sendMasterFeedback(ctx) {
            const input = document.getElementById('master_chat_input_' + ctx);
            const messagesDiv = document.getElementById('master_chat_messages_' + ctx);
            const msg = input.value.trim();
            if (!msg || !window.originalPromptForMaster) return;

            input.value = '';
            input.disabled = true;

            // Show user message
            messagesDiv.innerHTML += `<div class="flex justify-end"><div class="bg-brand-red/20 border border-brand-red/30 text-gray-200 text-[11px] font-mono py-2 px-3 rounded-xl rounded-tr-none max-w-[85%]">${msg}</div></div>`;

            // Loading
            const loadingId = 'mcloading_' + Date.now();
            messagesDiv.innerHTML += `<div id="${loadingId}" class="flex justify-start"><div class="bg-gray-800/60 text-gray-400 text-[11px] font-mono py-2 px-3 rounded-xl animate-pulse">Mestre pensando...</div></div>`;
            messagesDiv.scrollTop = messagesDiv.scrollHeight;

            try {
                const resp = await fetch('/admin/agents/master-chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        original_prompt: window.originalPromptForMaster,
                        current_improved_prompt: window.lastImprovedPrompt || '',
                        user_message: msg,
                        chat_history: masterChatHistory
                    })
                });
                const data = await resp.json();

                document.getElementById(loadingId)?.remove();

                if (data.success) {
                    // Save to history
                    masterChatHistory.push({ from: 'user', text: msg });
                    masterChatHistory.push({ from: 'master', text: data.response });

                    // Show master response
                    messagesDiv.innerHTML += `<div class="flex justify-start"><div class="bg-gray-800/60 border border-gray-700/50 text-gray-300 text-[11px] font-mono py-2 px-3 rounded-xl rounded-tl-none max-w-[85%] leading-relaxed">${formatMasterText(data.response)}</div></div>`;

                    // If master revised the prompt, show/update the apply button
                    if (data.updated_prompt) {
                        window.lastImprovedPrompt = data.updated_prompt;
                        showApplyBtn(ctx);
                        messagesDiv.innerHTML += `<div class="flex justify-center mt-1"><span class="text-[10px] text-brand-red font-mono bg-brand-red/10 px-2 py-1 rounded">✓ Prompt revisado com base no seu feedback</span></div>`;
                    }
                } else {
                    messagesDiv.innerHTML += `<div class="text-[10px] text-brand-red font-mono text-center">${data.error}</div>`;
                }
            } catch (e) {
                document.getElementById(loadingId)?.remove();
                messagesDiv.innerHTML += `<div class="text-[10px] text-brand-red font-mono text-center">Erro de conexão.</div>`;
            } finally {
                input.disabled = false;
                input.focus();
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }
        }


        function clearTestChat() {
            testHistory = [];
            const body = document.getElementById('test_chat_body');
            body.innerHTML = '<div class="bg-gray-800/50 border border-gray-700/50 rounded-lg p-3 text-xs text-gray-400 font-mono italic">Chat resetado. Inicie uma nova conversa ou clique em <strong class="text-brand-red">Simular com IA Mestre</strong>.</div>';
            document.getElementById('tester_analysis_result').classList.add('hidden');
        }

        async function runMasterSimulation() {
            const promptText = document.getElementById('ai_prompt').value;
            const btn = document.getElementById('btn_master_simulate');
            const body = document.getElementById('test_chat_body');
            const analysisDiv = document.getElementById('tester_analysis_result');
            const analysisContent = document.getElementById('tester_analysis_content');

            if (!promptText) {
                alert('Configure o System Prompt na aba Settings antes de simular.');
                return;
            }

            // Reset state
            testHistory = [];
            masterChatHistory = [];
            window.originalPromptForMaster = promptText;
            analysisDiv.classList.add('hidden');

            btn.disabled = true;
            btn.innerHTML = '<span class="animate-pulse">🤖 Simulando...</span>';

            body.innerHTML = `<div class="flex justify-center">
                <span class="text-[10px] text-brand-red font-mono bg-brand-red/10 border border-brand-red/20 px-3 py-1.5 rounded-full animate-pulse">
                    IA Mestre gerando simulação...
                </span>
            </div>`;

            try {
                const response = await fetch('/admin/agents/analyze-prompt', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt_text: promptText })
                });
                const data = await response.json();

                body.innerHTML = '';

                if (!data.success) {
                    body.innerHTML = `<div class="text-xs text-brand-red font-mono p-3">${data.error}</div>`;
                    return;
                }

                window.lastImprovedPrompt = data.improved_prompt;

                // Header da simulação
                body.innerHTML += `<div class="flex justify-center my-1">
                    <span class="text-[10px] text-gray-500 font-mono bg-gray-800/60 border border-gray-700/50 px-3 py-1 rounded-full">
                        — Simulação IA Mestre —
                    </span>
                </div>`;

                // Parsear o transcript em mensagens individuais
                if (data.simulation_transcript) {
                    const lines = data.simulation_transcript.split('\n').filter(l => l.trim());
                    for (const line of lines) {
                        if (line.startsWith('Lead:') || line.startsWith('lead:')) {
                            const text = line.replace(/^lead:/i, '').trim();
                            body.innerHTML += `<div class="flex justify-end"><div class="bg-brand-red text-white text-xs py-2 px-3 rounded-2xl rounded-tr-none min-w-0 max-w-[85%]">${text}</div></div>`;
                        } else if (line.startsWith('Agente:') || line.startsWith('agente:')) {
                            const text = line.replace(/^agente:/i, '').trim();
                            body.innerHTML += `<div class="flex justify-start"><div class="bg-gray-800 text-gray-200 text-xs py-2 px-3 rounded-2xl rounded-tl-none min-w-0 max-w-[85%] font-mono whitespace-pre-wrap">${text}</div></div>`;
                        } else if (line.trim()) {
                            // Linha sem prefixo reconhecido — exibe como separador informativo
                            body.innerHTML += `<div class="flex justify-center"><div class="text-[10px] text-gray-600 font-mono italic text-center">${line}</div></div>`;
                        }
                    }
                }

                // Separador antes do diagnóstico
                body.innerHTML += `<div class="flex justify-center my-1">
                    <span class="text-[10px] text-gray-500 font-mono bg-gray-800/60 border border-gray-700/50 px-3 py-1 rounded-full">
                        — Fim da Simulação —
                    </span>
                </div>`;
                body.scrollTop = body.scrollHeight;

                // Renderiza painel de diagnóstico abaixo do chat
                analysisContent.innerHTML =
                    `<div class="text-xs text-gray-300 font-mono leading-relaxed">${formatMasterText(data.analysis)}</div>`
                    + masterChatHtml('tester');

                if (data.improved_prompt) showApplyBtn('tester');
                analysisDiv.classList.remove('hidden');

            } catch (err) {
                body.innerHTML = `<div class="text-xs text-brand-red font-mono p-3">Erro de conexão ao simular.</div>`;
            } finally {
                btn.disabled = false;
                btn.innerHTML = '🤖 Simular com IA Mestre';
            }
        }

        async function runOptimizePrompt(mode) {
            const locationId = document.getElementById('ai_location_id').value;
            const channel = document.getElementById('ai_channel').value || 'whatsapp';
            const feedback = document.getElementById('optimize_feedback').value.trim();
            const status = document.getElementById('optimize_status');
            const resultBox = document.getElementById('tester_analysis_result');
            const resultTitle = document.getElementById('tester_analysis_title');
            const resultContent = document.getElementById('tester_analysis_content');
            const histInd = document.getElementById('optimize_history_indicator');

            status.classList.remove('hidden');
            resultBox.classList.add('hidden');

            try {
                const resp = await fetch(`/admin/agents/${locationId}/improve-prompt`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        mode,
                        channel,
                        feedback,
                        test_history: (typeof testHistory !== 'undefined' ? testHistory : []),
                    }),
                });
                const data = await resp.json();
                status.classList.add('hidden');

                if (!data.success) {
                    alert('Erro: ' + (data.error || 'desconhecido'));
                    return;
                }

                histInd.textContent = (
                    data.history_source === 'real' ? `${data.history_used} mensagens reais` :
                    data.history_source === 'test' ? `${data.history_used} mensagens do simulador` :
                    'Sem histórico'
                );

                if (mode === 'apply') {
                    document.getElementById('ai_prompt').value = data.prompt;
                    resultTitle.textContent = 'Prompt atualizado com sucesso';
                    resultContent.textContent = 'O novo prompt foi salvo no agente e preenchido na aba Config. Faça um novo teste no simulador para validar.';
                    resultBox.classList.remove('hidden');
                } else {
                    resultTitle.textContent = 'Diagnóstico da IA Mestre';
                    resultContent.textContent = data.diagnosis || '(sem retorno)';
                    resultBox.classList.remove('hidden');
                }
            } catch (e) {
                status.classList.add('hidden');
                alert('Erro de rede.');
            }
        }

        async function sendTestMessage() {
            const input = document.getElementById('test_chat_input');
            const body = document.getElementById('test_chat_body');
            const msg = input.value.trim();
            if (!msg) return;

            // Add user message
            addMessageToTester('me', msg);
            input.value = '';

            const locationId = document.getElementById('ai_location_id').value;
            const agentData = {
                prompt: document.getElementById('ai_prompt').value,
                model: document.getElementById('ai_model').value,
                api_key: document.getElementById('ai_api_key').value
            };

            // Loading bubble
            const loadingId = 'loading_' + Date.now();
            body.innerHTML += `<div id="${loadingId}" class="flex justify-start"><div class="bg-gray-800 text-gray-400 text-xs py-2 px-3 rounded-2xl animate-pulse">...</div></div>`;
            body.scrollTop = body.scrollHeight;

            try {
                const response = await fetch(`/admin/agents/${locationId}/test`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: msg, agent_data: agentData, history: testHistory })
                });
                const data = await response.json();

                document.getElementById(loadingId).remove();

                if (data.success) {
                    addMessageToTester('bot', data.response);
                    testHistory.push({ from: 'me', text: msg });
                    testHistory.push({ from: 'bot', text: data.response });
                } else {
                    addMessageToTester('system', data.error);
                }
            } catch (err) {
                document.getElementById(loadingId).remove();
                addMessageToTester('system', 'Erro de conexão com o simulador.');
            }
        }

        function addMessageToTester(from, text) {
            const body = document.getElementById('test_chat_body');
            let html = '';
            if (from === 'me') {
                html = `<div class="flex justify-end"><div class="bg-brand-red text-white text-xs py-2 px-3 rounded-2xl rounded-tr-none min-w-0 max-w-[85%]">${text}</div></div>`;
            } else if (from === 'bot') {
                html = `<div class="flex justify-start"><div class="bg-gray-800 text-gray-200 text-xs py-2 px-3 rounded-2xl rounded-tl-none min-w-0 max-w-[85%] font-mono whitespace-pre-wrap">${text}</div></div>`;
            } else {
                html = `<div class="flex justify-center"><div class="text-[10px] text-brand-red font-mono uppercase bg-brand-red/10 px-2 py-1 rounded text-center">${text}</div></div>`;
            }
            body.innerHTML += html;
            body.scrollTop = body.scrollHeight;
        }

        function _restoreVoicePlaceholder() {
            // Mostra a voz salva no dropdown ANTES de buscar a lista completa.
            // Assim o usuário vê que está salva e não pensa que sumiu.
            const voiceSelect = document.getElementById('ai_elevenlabs_voice_id');
            if (!voiceSelect) return;
            const preselected = voiceSelect.dataset.preselected || '';
            voiceSelect.innerHTML = '';
            if (preselected) {
                const opt = document.createElement('option');
                opt.value = preselected;
                opt.textContent = 'Voz salva (clique "Buscar Vozes" para ver nome)';
                opt.selected = true;
                voiceSelect.appendChild(opt);
                const optDefault = document.createElement('option');
                optDefault.value = '';
                optDefault.textContent = '-- Trocar voz: Busque vozes primeiro --';
                voiceSelect.appendChild(optDefault);
            } else {
                const optDefault = document.createElement('option');
                optDefault.value = '';
                optDefault.textContent = '-- Busque as Vozes Primeiro --';
                voiceSelect.appendChild(optDefault);
            }
        }

        async function fetchElevenLabsVoices() {
            const apiKey = document.getElementById('ai_elevenlabs_api_key').value;
            const btn = document.getElementById('btn_fetch_voices');
            const selectInfo = document.getElementById('voices_status_info');
            const voiceSelect = document.getElementById('ai_elevenlabs_voice_id');
            const preselected = voiceSelect.dataset.preselected;

            if (!apiKey) {
                alert("Please enter the ElevenLabs API Key first.");
                return;
            }

            btn.disabled = true;
            btn.innerHTML = '<span class="animate-pulse">Loading...</span>';
            selectInfo.innerText = "Fetching voices...";

            try {
                const response = await fetch(`/admin/agents/elevenlabs/voices?api_key=${encodeURIComponent(apiKey)}`);
                const data = await response.json();

                if (response.ok && data.success) {
                    voiceSelect.innerHTML = '<option value="">-- Selecione a Voz --</option>';
                    data.voices.forEach(v => {
                        const opt = document.createElement('option');
                        opt.value = v.voice_id;
                        opt.innerText = v.name;
                        if (v.voice_id === preselected) opt.selected = true;
                        voiceSelect.appendChild(opt);
                    });
                    selectInfo.innerText = `${data.voices.length} voices loaded successfully.`;
                    selectInfo.classList.replace('text-gray-400', 'text-green-400');
                } else {
                    selectInfo.innerText = data.detail || "Error loading voices.";
                    selectInfo.classList.replace('text-gray-400', 'text-red-400');
                }
            } catch (err) {
                selectInfo.innerText = "Connection error.";
                selectInfo.classList.replace('text-gray-400', 'text-red-400');
            } finally {
                btn.disabled = false;
                btn.innerHTML = 'Buscar Vozes';
            }
        }

        function _restoreFishVoicePlaceholder() {
            const sel = document.getElementById('ai_fishaudio_voice_id');
            if (!sel) return;
            const preselected = sel.dataset.preselected || '';
            sel.innerHTML = '';
            if (preselected) {
                const opt = document.createElement('option');
                opt.value = preselected;
                opt.textContent = 'Voz salva (clique "Buscar Vozes" para ver nome)';
                opt.selected = true;
                sel.appendChild(opt);
                const optDefault = document.createElement('option');
                optDefault.value = '';
                optDefault.textContent = '-- Trocar voz: Busque vozes primeiro --';
                sel.appendChild(optDefault);
            } else {
                const optDefault = document.createElement('option');
                optDefault.value = '';
                optDefault.textContent = '-- Busque as Vozes Primeiro --';
                sel.appendChild(optDefault);
            }
        }

        async function fetchFishAudioVoices() {
            const apiKey = document.getElementById('ai_fishaudio_api_key').value;
            const btn = document.getElementById('btn_fetch_fish_voices');
            const selectInfo = document.getElementById('fish_voices_status_info');
            const voiceSelect = document.getElementById('ai_fishaudio_voice_id');
            const preselected = voiceSelect.dataset.preselected;

            if (!apiKey) {
                alert("Informe a API Key do Fish Audio primeiro.");
                return;
            }

            btn.disabled = true;
            btn.innerHTML = '<span class="animate-pulse">Loading...</span>';
            selectInfo.innerText = "Buscando vozes...";

            try {
                const response = await fetch(`/admin/agents/fishaudio/voices?api_key=${encodeURIComponent(apiKey)}`);
                const data = await response.json();

                if (response.ok && data.success) {
                    voiceSelect.innerHTML = '<option value="">-- Selecione a Voz --</option>';
                    data.voices.forEach(v => {
                        const opt = document.createElement('option');
                        opt.value = v.voice_id;
                        const langs = (v.languages || []).join(',') || '?';
                        const stateTag = v.state && v.state !== 'trained' ? ` [${v.state}]` : '';
                        opt.innerText = `${v.name} (${langs})${stateTag}`;
                        if (v.voice_id === preselected) opt.selected = true;
                        voiceSelect.appendChild(opt);
                    });
                    selectInfo.innerText = `${data.voices.length} vozes carregadas.`;
                    selectInfo.classList.replace('text-gray-400', 'text-green-400');
                } else {
                    selectInfo.innerText = data.detail || "Erro ao carregar vozes.";
                    selectInfo.classList.replace('text-gray-400', 'text-red-400');
                }
            } catch (err) {
                selectInfo.innerText = "Erro de conexão.";
                selectInfo.classList.replace('text-gray-400', 'text-red-400');
            } finally {
                btn.disabled = false;
                btn.innerHTML = 'Buscar Vozes';
            }
        }

        function toggleTtsProviderBlocks() {
            const sel = document.getElementById('ai_tts_provider');
            if (!sel) return;
            const provider = sel.value || 'elevenlabs';
            const elBlock = document.getElementById('tts_block_elevenlabs');
            const fishBlock = document.getElementById('tts_block_fishaudio');
            if (elBlock) elBlock.classList.toggle('hidden', provider !== 'elevenlabs');
            if (fishBlock) fishBlock.classList.toggle('hidden', provider !== 'fishaudio');
        }

        // Move Voice and Knowledge tab content into collapsibles inside Config tab
        document.addEventListener('DOMContentLoaded', () => {
            const voiceTab = document.getElementById('ai_tab_voice');
            const voiceMount = document.getElementById('voice_mount_point');
            if (voiceTab && voiceMount) {
                while (voiceTab.firstChild) voiceMount.appendChild(voiceTab.firstChild);
                voiceTab.remove();
            }
            const knowledgeTab = document.getElementById('ai_tab_knowledge');
            const knowledgeMount = document.getElementById('knowledge_mount_point');
            if (knowledgeTab && knowledgeMount) {
                while (knowledgeTab.firstChild) knowledgeMount.appendChild(knowledgeTab.firstChild);
                knowledgeTab.remove();
            }

            // Abre o modal de sucesso do onboarding se a URL trouxer o link recém-gerado
            const params = new URLSearchParams(window.location.search);
            const obLink = params.get('onboarding_link');
            const obCompany = params.get('onboarding_company');
            if (obLink) {
                openOnboardingSuccessModal(obLink, obCompany || '');
                params.delete('onboarding_link');
                params.delete('onboarding_company');
                const clean = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
                window.history.replaceState({}, '', clean);
            }
        });
