/**
 Copyright 2011-2013 Red Hat, Inc.

 This software is licensed to you under the GNU General Public
 License as published by the Free Software Foundation; either version
 2 of the License (GPLv2) or (at your option) any later version.
 There is NO WARRANTY for this software, express or implied,
 including the implied warranties of MERCHANTABILITY,
 NON-INFRINGEMENT, or FITNESS FOR A PARTICULAR PURPOSE. You should
 have received a copy of GPLv2 along with this software; if not, see
 http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
*/

$(document).ready(function() {
  KT.subs.unsubSetup();
  KT.subs.subSetup();
  KT.subs.spinnerSetup();
  KT.subs.downloadSetup();

  $("#unsubscribeTable").treeTable({
    expandable: true,
    initialState: "collapsed",
    clickableNodeNames: true,
    onNodeShow: function(){$.sparkline_display_visible()}
  });

  $("#subscribeTable").treeTable({
    expandable: true,
    initialState: "collapsed",
    clickableNodeNames: true,
    onNodeShow: function(){$.sparkline_display_visible()}
  });

  (function() {
      $('.datepicker').datepicker();
      $('.datepicker').datepicker('option', 'dateFormat', 'yy-mm-dd');
      //$('.datepicker').datepicker('option', 'minDate', '2012-01-01');
      $('#start_date').datepicker('option', 'minDate', '2013-01-01');
  })();

});
