/**
 * Archivebate Video Browser - Application Context
 * Wydzielony centralny stan aplikacji oraz rejestr elementów DOM.
 */

(function (global) {
  'use strict';

  // Centralny stan aplikacji
  const state = {
    mode: 'home', // 'home' | 'search' | 'model' | 'favorites' | 'history' | 'following' | 'account'
    currentQuery: '',
    currentModel: '',
    currentPage: 1,
    lastPage: 50,
    isLoading: false,
    videos: [],
    currentVideoDetails: null,
    isIframeMode: false,
    targetCheckpointId: null,
    groupByAuthor: (typeof localStorage !== 'undefined' ? localStorage.getItem('archivebate_group_by_author') === 'true' : false),
    gridCardMap: new Map(),
    lastAppliedFeedRevision: -1,
    lastAppliedFeedUpdatedAt: 0,
    lastAppliedFeedVideoCount: -1,
    lastAppliedVideosCount: 0,
    feedRefreshing: false,
    feedSnapshotId: null,
    feedSpecKey: null,
    gridController: null,
    viewController: null,
    gridGeneration: 0,
    viewGeneration: 0,
    playerGeneration: 0,
    previewSeeker: null,
    activeSearchSource: null,
    lastClickedGridVideo: null,
    lastClickedGridIndex: -1,
    showCamwhores: (typeof localStorage !== 'undefined' ? localStorage.getItem('archivebate_show_camwhores') !== 'false' : true),
    sourceFilter: (typeof localStorage !== 'undefined' ? (localStorage.getItem('archivebate_source_filter') || 'all') : 'all'),
    authorFilter: (typeof localStorage !== 'undefined' ? (localStorage.getItem('archivebate_author_filter') || 'all') : 'all'), // 'all' | 'only_fav' | 'exclude_fav'
    favoritesCount: 0,
    historyCount: 0,
    followingCount: 0,
    favoriteAuthors: new Set(),
    preloadedStoryboard: [],
    localStoryboard: null,
    currentStoryboardKey: null,
    storyboardBuildController: null,
    timelineSpriteBoard: null,
    timelineSpriteAbort: null,
    videoById: new Map()
  };

  function createDomRegistry() {
    const getEl = (id) => (typeof document !== 'undefined' && document.getElementById ? document.getElementById(id) : null);
    return {
      videoGrid: getEl('videoGrid'),
      searchInput: getEl('searchInput'),
      clearSearchBtn: getEl('clearSearchBtn'),
      searchAutocompleteDropdown: getEl('searchAutocompleteDropdown'),
      tagsSection: getEl('tagsSection'),
      tagsContainer: getEl('tagsContainer'),
      contentHeader: getEl('contentHeader'),
      viewTitle: getEl('viewTitle'),
      videoCount: getEl('videoCount'),
      resetFilterBtn: getEl('resetFilterBtn'),
      toggleCamwhoresBtn: getEl('toggleCamwhoresBtn'),
      sourceToggleIcon: getEl('sourceToggleIcon'),
      camwhoresToggleLabel: getEl('camwhoresToggleLabel'),
      toggleAuthorFilterBtn: getEl('toggleAuthorFilterBtn'),
      authorFilterIcon: getEl('authorFilterIcon'),
      authorFilterLabel: getEl('authorFilterLabel'),
      toggleGroupBtn: getEl('toggleGroupBtn'),
      groupToggleIcon: getEl('groupToggleIcon'),
      groupToggleLabel: getEl('groupToggleLabel'),
      feedRefreshIndicator: getEl('feedRefreshIndicator'),
      headerCheckpointBtn: getEl('headerCheckpointBtn'),
      checkpointText: getEl('checkpointText'),
      navCheckpointBtn: getEl('navCheckpointBtn'),
      matchedProfiles: getEl('matchedProfiles'),
      profilesList: getEl('profilesList'),
      // Home Stats Bar
      homeStatsBar: getEl('homeStatsBar'),
      statGlobalVideos: getEl('statGlobalVideos'),
      statCatalogVideos: getEl('statCatalogVideos'),
      statCatalogVideosLbl: getEl('statCatalogVideosLbl'),
      statGlobalProfiles: getEl('statGlobalProfiles'),
      statPageVideos: getEl('statPageVideos'),
      statUserLibrary: getEl('statUserLibrary'),
      statBlockedInfo: getEl('statBlockedInfo'),
      statBlockedVideosLbl: getEl('statBlockedVideosLbl'),
      // Account Panel View
      accountPanelView: getEl('accountPanelView'),
      panelEmail: getEl('panelEmail'),
      panelLastSync: getEl('panelLastSync'),
      panelSyncBtn: getEl('panelSyncBtn'),
      panelClearHistoryBtn: getEl('panelClearHistoryBtn'),
      statFavCount: getEl('statFavCount'),
      statHistCount: getEl('statHistCount'),
      statFollCount: getEl('statFollCount'),
      statBlockedCount: getEl('statBlockedCount'),
      statBtnFavs: getEl('statBtnFavs'),
      statBtnHist: getEl('statBtnHist'),
      statBtnFoll: getEl('statBtnFoll'),
      statBtnBlocked: getEl('statBtnBlocked'),
      // Nav Tabs
      navHomeBtn: getEl('navHomeBtn'),
      navFavoritesBtn: getEl('navFavoritesBtn'),
      navHistoryBtn: getEl('navHistoryBtn'),
      navFollowingBtn: getEl('navFollowingBtn'),
      navAccountBtn: getEl('navAccountBtn'),
      navFavCount: getEl('navFavCount'),
      navHistCount: getEl('navHistCount'),
      // Pagination
      paginationSection: getEl('paginationSection'),
      prevPageBtn: getEl('prevPageBtn'),
      nextPageBtn: getEl('nextPageBtn'),
      lastPageBtn: getEl('lastPageBtn'),
      lastPageNumber: getEl('lastPageNumber'),
      pageNumbersList: getEl('pageNumbersList'),
      pageJumpInput: getEl('pageJumpInput'),
      pageJumpBtn: getEl('pageJumpBtn'),
      paginationSectionTop: getEl('paginationSectionTop'),
      prevPageBtnTop: getEl('prevPageBtnTop'),
      nextPageBtnTop: getEl('nextPageBtnTop'),
      lastPageBtnTop: getEl('lastPageBtnTop'),
      lastPageNumberTop: getEl('lastPageNumberTop'),
      pageNumbersListTop: getEl('pageNumbersListTop'),
      pageJumpInputTop: getEl('pageJumpInputTop'),
      pageJumpBtnTop: getEl('pageJumpBtnTop'),
      // User
      userEmail: getEl('userEmail'),
      statusDot: getEl('statusDot'),
      reloginBtn: getEl('reloginBtn'),
      logoBtn: getEl('logoBtn'),
      // Modal
      videoModal: getEl('videoModal'),
      modalCloseBtn: getEl('modalCloseBtn'),
      modalVideo: getEl('modalVideo'),
      modalIframe: getEl('modalIframe'),
      modalLoadingPoster: getEl('modalLoadingPoster'),
      videoLoader: getEl('videoLoader'),
      modalPlatform: getEl('modalPlatform'),
      modalModelName: getEl('modalModelName'),
      modalFavBtn: getEl('modalFavBtn'),
      modalViewModelVideosBtn: getEl('modalViewModelVideosBtn'),
      modalBlockModelBtn: getEl('modalBlockModelBtn'),
      modalDownloadBtn: getEl('modalDownloadBtn'),
      modalPopoutBtn: getEl('modalPopoutBtn'),
      modalTogglePlayerBtn: getEl('modalTogglePlayerBtn'),
      modalOriginalBtn: getEl('modalOriginalBtn'),
      modalKeywords: getEl('modalKeywords'),
      toastContainer: getEl('toastContainer'),
      // Modal Custom Player Controls
      modalPlayerWrapper: getEl('modalPlayerWrapper'),
      modalCenterPlay: getEl('modalCenterPlay'),
      modalControlsBar: getEl('modalControlsBar'),
      modalTimelineContainer: getEl('modalTimelineContainer'),
      modalTimelineTooltip: getEl('modalTimelineTooltip'),
      modalTimelinePreviewBox: getEl('modalTimelinePreviewBox'),
      modalTimelinePreviewImg: getEl('modalTimelinePreviewImg'),
      modalTimelineSprite: getEl('modalTimelineSprite'),
      modalTimelinePreviewStatus: getEl('modalTimelinePreviewStatus'),
      modalTimelinePreviewVideo: getEl('modalTimelinePreviewVideo'),
      modalTimelineTimeText: getEl('modalTimelineTimeText'),
      modalTimelineProgress: getEl('modalTimelineProgress'),
      modalTimelineBuffer: getEl('modalTimelineBuffer'),
      modalTimelineThumb: getEl('modalTimelineThumb'),
      modalCtrlPlayBtn: getEl('modalCtrlPlayBtn'),
      modalCtrlRewindBtn: getEl('modalCtrlRewindBtn'),
      modalCtrlForwardBtn: getEl('modalCtrlForwardBtn'),
      modalCtrlPrevVideoBtn: getEl('modalCtrlPrevVideoBtn'),
      modalCtrlNextVideoBtn: getEl('modalCtrlNextVideoBtn'),
      modalNavPrevArrow: getEl('modalNavPrevArrow'),
      modalNavNextArrow: getEl('modalNavNextArrow'),
      modalCtrlVolumeBtn: getEl('modalCtrlVolumeBtn'),
      modalCtrlVolumeSlider: getEl('modalCtrlVolumeSlider'),
      modalCtrlTimeDisplay: getEl('modalCtrlTimeDisplay'),
      modalCtrlSpeedBtn: getEl('modalCtrlSpeedBtn'),
      modalCtrlPipBtn: getEl('modalCtrlPipBtn'),
      modalCtrlFullscreenBtn: getEl('modalCtrlFullscreenBtn')
    };
  }

  // Elementy DOM
  const dom = createDomRegistry();

  function initDom() {
    const updated = createDomRegistry();
    Object.assign(dom, updated);
    return dom;
  }

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', initDom);
  }

  const ArchivebateAppContext = {
    state,
    dom,
    initDom
  };

  global.ArchivebateAppContext = ArchivebateAppContext;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateAppContext;
  }
})(typeof window !== 'undefined' ? window : globalThis);
