<template>
  <n-config-provider :theme="theme">
    <n-message-provider>
      <n-dialog-provider>
        <router-view />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { computed } from 'vue'
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme } from 'naive-ui'
import { useOsTheme } from 'naive-ui'

const osThemeRef = useOsTheme()
const theme = computed(() => osThemeRef.value === 'dark' ? darkTheme : null)
</script>
