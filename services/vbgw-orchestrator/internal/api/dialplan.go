/**
 * @file dialplan.go
 * @description mod_xml_curl을 처리하기 위한 동적 라우팅 엔진 엔드포인트
 */

package api

import (
	"fmt"
	"log/slog"
	"net/http"
)

// DialplanHandler handles mod_xml_curl dialplan requests.
type DialplanHandler struct {
	// RuleEngine could be injected here later (e.g. database or config)
}

// GenerateDialplan handles POST /api/v1/fs/dialplan
func (h *DialplanHandler) GenerateDialplan(w http.ResponseWriter, r *http.Request) {
	// FreeSWITCH sends data as x-www-form-urlencoded
	if err := r.ParseForm(); err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}

	// Dump debug for incoming parameters
	callerID := r.FormValue("Caller-Caller-ID-Number")
	destNum := r.FormValue("Caller-Destination-Number")
	huntCtx := r.FormValue("Hunt-Context")
	
	slog.Info("Dynamic Dialplan request", "caller_id", callerID, "dest", destNum, "context", huntCtx)

	// Since we only want to handle the "default" context inbound traffic here:
	if huntCtx != "default" {
		w.Write([]byte(`<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<document type="freeswitch/xml">
  <section name="result">
    <result status="not found"/>
  </section>
</document>`))
		return
	}

	// Generate the complete dialplan logic mimicking the static default.xml
	xmlPayload := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<document type="freeswitch/xml">
  <section name="dialplan" description="Dynamic Routing API">
    <context name="default">
      <extension name="voicebot-inbound">
        <condition field="destination_number" expression="^.*$">
          <action application="set" data="jitterbuffer_msec=60:120:20"/>
          
          <action application="set" data="capacity_full=${sip_h_X-VBGW-Capacity-Full}"/>
          <condition field="${capacity_full}" expression="^true$">
            <action application="valet_park" data="ai_queue 8000"/>
          </condition>

          <action application="ring_ready"/>
          
          <action application="agc" data="1"/>
          <action application="start_dtmf"/>
          
          <action application="set" data="audio_fork_url=$${audio_fork_scheme}://$${bridge_host}:$${bridge_ws_port}/audio/${uuid}"/>
          <action application="audio_fork" data="${audio_fork_url} 16000 mono"/>
          
          <action application="answer"/>
          <action application="set" data="park_timeout=5"/>
          <action application="park"/>
          
          <!-- Failover -->
          <action application="transfer" data="agent_transfer XML default"/>
        </condition>
      </extension>

      <extension name="agent_transfer">
        <condition field="destination_number" expression="^agent_transfer$">
          <action application="stop_audio_fork"/>
          <action application="set" data="callcenter_name=ai_support_queue"/>
          <action application="callcenter" data="${callcenter_name}"/>
        </condition>
      </extension>
    </context>
  </section>
</document>`)

	w.Header().Set("Content-Type", "application/xml")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(xmlPayload))
}
